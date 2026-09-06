#!/usr/bin/env bash
# Put the HTTP front end on Lambda, behind a public Function URL.
#
#   deploy/deploy.sh              build, push, and create or update everything
#
# Idempotent: run it again after a change and it updates the function in place.
#
# Why Lambda rather than a server. The checklist is not a service that needs to
# be up -- it is a function that runs when somebody asks a question, takes a
# couple of seconds, and then has nothing to do. Anything with a machine
# underneath it bills for the waiting: the cheapest always-on options land
# around $3-5/month before traffic, and most of that is the idle. Lambda bills
# per millisecond of actual work, and the always-free tier is 400,000
# GB-seconds a month -- roughly 85,000 validations at this memory size. What is
# left is about five cents a month of ECR storage for the image.
#
# A Function URL rather than API Gateway for the same reason, and one more: an
# HTTP API costs $1 per million requests on top of Lambda, and it cuts a
# response off at 29 seconds. A batch of symbols can outlast that. A Function
# URL costs nothing and allows up to 15 minutes.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${TRADEVAL_FUNCTION:-tradeval}"
ROLE="${FUNCTION}-lambda-role"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
IMAGE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${FUNCTION}:latest"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 2048MB is not about memory -- /health peaks at under 200MB and a validation
# does not come close to the ceiling. Lambda scales CPU with the memory you
# ask for, and the import of pandas, numpy and lxml is the whole cold start.
# At 1024MB that import takes about 3 seconds; at 2048MB it takes 1.5, and
# since you are billed for duration x memory, the faster-and-fatter option
# costs about the same and answers twice as quickly.
MEMORY=2048
# Long enough for /validate/batch across a handful of symbols. A single symbol
# answers in two or three seconds; this is the ceiling, not the expectation,
# and an idle function costs nothing regardless of what the ceiling says.
TIMEOUT=120

say() { printf '\n== %s\n' "$1"; }

say "ECR repository"
aws ecr describe-repositories --repository-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1 || {
    aws ecr create-repository --repository-name "$FUNCTION" --region "$REGION" \
        --image-scanning-configuration scanOnPush=false >/dev/null
    # ECR bills for every image it holds, and a deploy leaves the old one
    # behind untagged. Three is enough to roll back to.
    aws ecr put-lifecycle-policy --repository-name "$FUNCTION" --region "$REGION" \
        --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"Keep the last 3 images.","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}' >/dev/null
}

say "Build and push $IMAGE"
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
# --provenance/--sbom off: buildx would otherwise push a manifest list with an
# attestation in it, and Lambda rejects an image it cannot resolve to one
# platform. arm64 to match the function's architecture, and for Graviton's
# cheaper per-GB-second rate.
docker build --platform linux/arm64 --provenance=false --sbom=false \
    -f "$ROOT/deploy/Dockerfile" -t "$IMAGE" --push "$ROOT"

say "Execution role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    aws iam create-role --role-name "$ROLE" \
        --description "Execution role for the $FUNCTION Lambda: CloudWatch Logs only." \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    # Writing its own logs is the only thing the function needs to be allowed
    # to do. It reads Yahoo and Kalshi over the open internet, and it has no
    # database, no bucket and no secret to reach for.
    aws iam attach-role-policy --role-name "$ROLE" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    sleep 10  # IAM is eventually consistent; Lambda will refuse a role it cannot see yet.
fi
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"

say "Shared secret"
# Precedence: what you exported, then what the deployed function already has,
# then a fresh one. The middle case is the important one -- a redeploy must not
# silently rotate the key out from under whatever is already calling this.
KEY="${TRADEVAL_API_KEY:-}"
if [ -z "$KEY" ]; then
    KEY="$(aws lambda get-function-configuration --function-name "$FUNCTION" --region "$REGION" \
        --query 'Environment.Variables.TRADEVAL_API_KEY' --output text 2>/dev/null || true)"
    [ "$KEY" = "None" ] && KEY=""
fi
if [ -z "$KEY" ]; then
    KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    NEW_KEY=1
    echo "  generated a new one"
else
    NEW_KEY=0
    echo "  keeping the existing one"
fi

say "Function"
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FUNCTION" --region "$REGION" \
        --image-uri "$IMAGE" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$REGION"
    aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
        --memory-size "$MEMORY" --timeout "$TIMEOUT" \
        --environment "Variables={PYTHONUNBUFFERED=1,TRADEVAL_API_KEY=$KEY}" >/dev/null
else
    aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
        --package-type Image --code "ImageUri=$IMAGE" --role "$ROLE_ARN" \
        --architectures arm64 --memory-size "$MEMORY" --timeout "$TIMEOUT" \
        --environment "Variables={PYTHONUNBUFFERED=1,TRADEVAL_API_KEY=$KEY}" \
        --description "The trade checklist over HTTP." >/dev/null
fi
aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$REGION"

# Logs are the one thing here that grows without being asked to, and the
# default retention is forever.
aws logs put-retention-policy --log-group-name "/aws/lambda/$FUNCTION" \
    --region "$REGION" --retention-in-days 7 2>/dev/null || true

say "Public URL"
# x-tradeval-key has to be an allowed header: the Function URL answers the
# browser's preflight itself, and a header it does not list is one no browser
# will send.
CORS='{"AllowOrigins":["*"],"AllowMethods":["GET","POST"],"AllowHeaders":["content-type","x-tradeval-key"],"MaxAge":86400}'
if aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
    aws lambda update-function-url-config --function-name "$FUNCTION" --region "$REGION" \
        --auth-type NONE --cors "$CORS" >/dev/null
else
    aws lambda create-function-url-config --function-name "$FUNCTION" --region "$REGION" \
        --auth-type NONE --cors "$CORS" >/dev/null
fi

# Auth type NONE means Lambda checks nothing, but the resource policy is still
# consulted -- and since October 2025 a Function URL needs *two* statements
# rather than one. The console writes both when you tick the box; the CLI
# writes neither. Without the second, every request is a 403 that looks
# exactly like a misconfigured auth type.
#
# InvokedViaFunctionUrl on the second statement is what keeps "public" meaning
# public over the URL: without it, Principal "*" on lambda:InvokeFunction lets
# any AWS account in the world call the function directly through the API.
aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
    --statement-id FunctionURLAllowPublicAccess \
    --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE >/dev/null 2>&1 || true
aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
    --statement-id FunctionURLInvokeAllowPublicAccess \
    --action lambda:InvokeFunction --principal "*" --invoked-via-function-url >/dev/null 2>&1 || true

URL="$(aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" --query FunctionUrl --output text)"
URL="${URL%/}"

say "Deployed"
printf '  %s\n\n' "$URL"
printf "  curl -s %s/health -H 'x-tradeval-key: \$TRADEVAL_API_KEY'\n" "$URL"
printf "  curl -s %s/validate -H 'x-tradeval-key: \$TRADEVAL_API_KEY' \\\\\n" "$URL"
printf "      -H 'content-type: application/json' \\\\\n"
printf "      -d '{\"symbol\": \"NVDA\", \"strategy\": \"short\", \"instrument\": \"stock\"}'\n\n"

# Printed once, on the deploy that makes it. After that it is only in the
# function's configuration -- read it back with:
#   aws lambda get-function-configuration --function-name tradeval \
#       --query 'Environment.Variables.TRADEVAL_API_KEY' --output text
if [ "$NEW_KEY" = "1" ]; then
    printf '  The key, which is not printed again:\n\n    %s\n\n' "$KEY"
fi

say "Checking it answers"
curl -fsS --max-time 120 -H "x-tradeval-key: $KEY" "$URL/health" && printf '\n'
printf '  and turns away a request without the key: '
curl -s -o /dev/null -w '%{http_code}\n' --max-time 120 "$URL/health"
