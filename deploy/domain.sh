#!/usr/bin/env bash
# Put the service on a name of your own.
#
#   deploy/domain.sh                 once the domain is registered
#
# Run deploy.sh first: this reads the Function URL it made and puts a name in
# front of it. Safe to re-run, and designed to be: it stops at each point where
# a DNS record has to be added by hand, tells you exactly what to add, and
# waits. Run it again after adding one and it picks up where it left off.
#
# A Lambda Function URL cannot answer on your domain by itself -- the hostname
# is assigned by AWS and there is no setting to change it. Something has to sit
# in front and hold the certificate, and CloudFront is the option that costs
# nothing at this size: the always-free tier is 1TB of transfer and 10 million
# requests a month, against an API that answers in 18KB. API Gateway, the
# alternative, bills $1 per million requests and would put back the 29-second
# response ceiling a Function URL was chosen to avoid.
#
# DNS is wherever you registered the domain. If that is Route 53 this script
# writes the records itself; if it is anywhere else -- GoDaddy, Cloudflare,
# Namecheap -- it prints them for you to paste, which is also the arrangement
# that avoids paying $0.50/month for a hosted zone you would only use twice.
set -euo pipefail

REGION="us-east-1"          # ACM certificates for CloudFront must live here.
FUNCTION="${TRADEVAL_FUNCTION:-tradeval}"
DOMAIN="${TRADEVAL_DOMAIN:-trade-validation.com}"
HOST="${TRADEVAL_HOST:-api.$DOMAIN}"

say()  { printf '\n== %s\n' "$1"; }
todo() { printf '\n-- Add this record at your registrar, then run this script again:\n\n    %-6s %s\n    %-6s %s\n\n' "type" "$1" "name" "$2"; printf '    %-6s %s\n\n' "value" "$3"; }

say "Origin"
ORIGIN="$(aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" \
    --query FunctionUrl --output text)"
ORIGIN="${ORIGIN#https://}"; ORIGIN="${ORIGIN%/}"
echo "  $ORIGIN"

# A Route 53 zone is optional. Without one, records are yours to add.
ZONE="$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN." \
    --query "HostedZones[?Name=='$DOMAIN.'].Id | [0]" --output text 2>/dev/null || echo None)"
[ "$ZONE" = "None" ] && ZONE="" || ZONE="${ZONE#/hostedzone/}"

upsert() {  # type name value -- through Route 53 if we have it, by hand if not
    if [ -n "$ZONE" ]; then
        aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch "{
            \"Changes\": [{\"Action\": \"UPSERT\", \"ResourceRecordSet\": {
                \"Name\": \"$2\", \"Type\": \"$1\", \"TTL\": 300,
                \"ResourceRecords\": [{\"Value\": \"$3\"}]}}]}" >/dev/null
        echo "  wrote $1 $2"
        return 0
    fi
    return 1
}

say "Certificate for $HOST"
CERT="$(aws acm list-certificates --region "$REGION" \
    --query "CertificateSummaryList[?DomainName=='$HOST'].CertificateArn | [0]" --output text)"
if [ "$CERT" = "None" ] || [ -z "$CERT" ]; then
    # DNS validation rather than email: it needs no mailbox, and it renews
    # itself for as long as the record stays put. A certificate that quietly
    # expires in a year is the classic way for a thing like this to break.
    CERT="$(aws acm request-certificate --region "$REGION" --domain-name "$HOST" \
        --validation-method DNS --key-algorithm RSA_2048 \
        --query CertificateArn --output text)"
    echo "  requested $CERT"
fi

STATUS="$(aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT" \
    --query Certificate.Status --output text)"
if [ "$STATUS" != "ISSUED" ]; then
    # ACM fills the validation record in asynchronously; it is not there the
    # instant the certificate is.
    for _ in $(seq 1 20); do
        VN="$(aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT" \
            --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text)"
        [ -n "$VN" ] && [ "$VN" != "None" ] && break
    done
    VV="$(aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT" \
        --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Value' --output text)"
    if ! upsert CNAME "$VN" "$VV"; then
        # Already added? Then there is nothing to ask for -- wait instead.
        # Checking public DNS rather than trusting a flag means a re-run does
        # the right thing whether or not the record has landed yet.
        LIVE="$(dig +short CNAME "${VN%.}" | head -1)"
        if [ "${LIVE%.}" != "${VV%.}" ]; then
            # GoDaddy and most registrars want the name relative to the domain,
            # without the trailing dot and without the domain on the end.
            SHORT="${VN%.$DOMAIN.}"
            say "The certificate is waiting on proof that you own $HOST"
            todo "CNAME" "$SHORT" "${VV%.}"
            echo "  ACM usually picks it up within a few minutes of it going live."
            exit 0
        fi
        echo "  validation record is live in DNS"
    fi
    echo "  waiting for ACM to read it"
    aws acm wait certificate-validated --region "$REGION" --certificate-arn "$CERT"
fi
echo "  issued"

say "CloudFront distribution"
DIST="$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?Aliases.Items && contains(Aliases.Items, '$HOST')].Id | [0]" \
    --output text 2>/dev/null || echo None)"
if [ "$DIST" = "None" ] || [ -z "$DIST" ]; then
    # Two managed policies do the real work here, and both matter:
    #
    #   CachingDisabled            the answers are live market data and half
    #                              the endpoints are POSTs. A cache would hand
    #                              back yesterday's tape.
    #   AllViewerExceptHostHeader  forwards x-tradeval-key and content-type to
    #                              the origin. CloudFront drops most headers by
    #                              default, which would turn every request
    #                              through the domain into a 401 while the raw
    #                              Function URL kept working. It leaves Host
    #                              alone, so the Function URL still sees its own
    #                              hostname, which is what it routes on.
    CACHE=4135ea2d-6df8-44a3-9df3-4b5a84be39ad
    ORIGIN_REQ=b689b0a8-53d0-40ab-baf2-68738e2966ac
    # CloudFront's view of ACM lags issuance by a minute or two, and the error
    # it gives meanwhile -- "the certificate doesn't exist, isn't in us-east-1,
    # isn't valid" -- describes none of what is actually wrong. Retry rather
    # than send the reader off to check three things that are all fine.
    for attempt in $(seq 1 10); do
    DIST="$(aws cloudfront create-distribution --distribution-config "{
        \"CallerReference\": \"$FUNCTION-$(date +%s)\",
        \"Comment\": \"$HOST -> the tradeval Function URL\",
        \"Enabled\": true,
        \"Aliases\": {\"Quantity\": 1, \"Items\": [\"$HOST\"]},
        \"Origins\": {\"Quantity\": 1, \"Items\": [{
            \"Id\": \"lambda\", \"DomainName\": \"$ORIGIN\",
            \"CustomOriginConfig\": {\"HTTPPort\": 80, \"HTTPSPort\": 443,
                \"OriginProtocolPolicy\": \"https-only\",
                \"OriginSslProtocols\": {\"Quantity\": 1, \"Items\": [\"TLSv1.2\"]},
                \"OriginReadTimeout\": 60, \"OriginKeepaliveTimeout\": 5}}]},
        \"DefaultCacheBehavior\": {
            \"TargetOriginId\": \"lambda\",
            \"ViewerProtocolPolicy\": \"redirect-to-https\",
            \"AllowedMethods\": {\"Quantity\": 7,
                \"Items\": [\"GET\",\"HEAD\",\"OPTIONS\",\"PUT\",\"POST\",\"PATCH\",\"DELETE\"],
                \"CachedMethods\": {\"Quantity\": 2, \"Items\": [\"GET\",\"HEAD\"]}},
            \"CachePolicyId\": \"$CACHE\",
            \"OriginRequestPolicyId\": \"$ORIGIN_REQ\",
            \"Compress\": true},
        \"ViewerCertificate\": {\"ACMCertificateArn\": \"$CERT\",
            \"SSLSupportMethod\": \"sni-only\", \"MinimumProtocolVersion\": \"TLSv1.2_2021\"},
        \"PriceClass\": \"PriceClass_100\"
    }" --query Distribution.Id --output text 2>/dev/null)" && break
        [ "$attempt" = "10" ] && { echo "  CloudFront still will not take the certificate" >&2; exit 1; }
        echo "  certificate not visible to CloudFront yet, retrying ($attempt)"
        aws acm wait certificate-validated --region "$REGION" --certificate-arn "$CERT" 2>/dev/null || true
    done
    echo "  created $DIST"
else
    echo "  $DIST"
fi

CF="$(aws cloudfront get-distribution --id "$DIST" --query Distribution.DomainName --output text)"
echo "  $CF"

say "Pointing $HOST at it"
RESOLVED="$(dig +short CNAME "$HOST" | head -1)"
if [ "${RESOLVED%.}" = "$CF" ]; then
    echo "  already points there"
elif [ -n "$ZONE" ]; then
    # An alias record through Route 53: free to resolve, and the only kind
    # allowed at an apex should this ever move to the bare domain.
    # Z2FDTNDATAQYW2 is CloudFront's fixed zone id -- the same in every account.
    aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch "{
        \"Changes\": [{\"Action\": \"UPSERT\", \"ResourceRecordSet\": {
            \"Name\": \"$HOST.\", \"Type\": \"A\",
            \"AliasTarget\": {\"HostedZoneId\": \"Z2FDTNDATAQYW2\",
                \"DNSName\": \"$CF\", \"EvaluateTargetHealth\": false}}}]}" >/dev/null
    echo "  $HOST -> $CF"
else
    todo "CNAME" "${HOST%.$DOMAIN}" "$CF"
    echo "  Then run this script again to check it end to end."
    exit 0
fi

say "Waiting for the distribution to deploy (a few minutes the first time)"
aws cloudfront wait distribution-deployed --id "$DIST"

say "Done"
printf '  https://%s/health\n\n' "$HOST"
printf "  curl -s https://%s/health -H 'x-tradeval-key: \$TRADEVAL_API_KEY'\n\n" "$HOST"
