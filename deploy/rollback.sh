#!/usr/bin/env bash
# Put an earlier release of the Lambda back live.
#
#   deploy/rollback.sh               list the published releases, newest first
#   deploy/rollback.sh v1.1.0        go back to the release built from that tag
#   deploy/rollback.sh 7             go back to Lambda version 7
#
# Every deploy publishes a numbered Lambda version described as
# "<git version> (<commit>)" and pushes its image under the git version, so a
# release can be named either way. Rolling back points the function at that
# release's exact image, by digest, and publishes one more version recording
# that it was a rollback -- the list stays a true history of what ran when.
#
# Only the image changes. Configuration -- memory, timeout, the shared key --
# stays as the latest deploy left it, which is what you want when the code was
# the problem. ECR keeps the last ten images; a release older than that is
# listed but can no longer be restored.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${TRADEVAL_FUNCTION:-tradeval}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${FUNCTION}"

if [ $# -eq 0 ]; then
    CURRENT="$(aws lambda get-function --function-name "$FUNCTION" --region "$REGION" \
        --query 'Code.ResolvedImageUri' --output text)"
    echo "Published releases of $FUNCTION, newest first (* is live):"
    aws lambda list-versions-by-function --function-name "$FUNCTION" --region "$REGION" \
        --query 'Versions[?Version!=`$LATEST`].[Version,Description,LastModified]' --output text \
        | sort -rn | while IFS=$'\t' read -r number description modified; do
            image="$(aws lambda get-function --function-name "$FUNCTION" --qualifier "$number" --region "$REGION" \
                --query 'Code.ResolvedImageUri' --output text 2>/dev/null || true)"
            mark=" "; [ "$image" = "$CURRENT" ] && mark="*"
            printf '  %s %-4s %-32s %s\n' "$mark" "$number" "$description" "${modified%%.*}"
        done
    echo
    echo "Roll back with: deploy/rollback.sh <version number or tag>"
    exit 0
fi

TARGET="$1"
if [[ "$TARGET" =~ ^[0-9]+$ ]]; then
    # A Lambda version holds the digest of the image it ran, which survives
    # the tag it was pushed under being moved on.
    IMAGE="$(aws lambda get-function --function-name "$FUNCTION" --qualifier "$TARGET" --region "$REGION" \
        --query 'Code.ResolvedImageUri' --output text)"
    LABEL="$(aws lambda get-function-configuration --function-name "$FUNCTION" --qualifier "$TARGET" --region "$REGION" \
        --query Description --output text)"
else
    DIGEST="$(aws ecr describe-images --repository-name "$FUNCTION" --region "$REGION" \
        --image-ids imageTag="$TARGET" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)"
    if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
        echo "No image tagged $TARGET in ECR. Run deploy/rollback.sh with no argument to list releases." >&2
        exit 1
    fi
    IMAGE="${REPO}@${DIGEST}"
    LABEL="$TARGET"
fi

echo "== Rolling $FUNCTION back to $LABEL"
echo "   $IMAGE"
aws lambda update-function-code --function-name "$FUNCTION" --region "$REGION" --image-uri "$IMAGE" >/dev/null
aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$REGION"
PUBLISHED="$(aws lambda publish-version --function-name "$FUNCTION" --region "$REGION" \
    --description "rollback to $LABEL" --query Version --output text)"
echo "   published Lambda version $PUBLISHED: rollback to $LABEL"

URL="$(aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" --query FunctionUrl --output text)"
KEY="$(aws lambda get-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --query 'Environment.Variables.TRADEVAL_API_KEY' --output text)"
echo "== Checking it answers"
curl -fsS --max-time 60 -H "x-tradeval-key: $KEY" "${URL%/}/health" && printf '\n'
