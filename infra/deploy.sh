#!/usr/bin/env bash
# Deploy one environment's stack from the working tree (run from anywhere in the repo).
#
#   infra/deploy.sh dev                      # flight-dev, SiteUrl defaults to http://localhost:8080
#   SITE_URL=https://<site> infra/deploy.sh prod   # flight-m1 (the live stack)
#
# GitHub Actions runs this on every push: dev -> flight-dev, main -> flight-m1
# (.github/workflows/deploy.yml). Run it by hand to try backend changes on flight-dev
# before you push. Needs AWS credentials for the account (aws sts get-caller-identity).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

ENV="${1:-}"
case "$ENV" in
  dev)  STACK=flight-dev; SITE_URL="${SITE_URL:-http://localhost:8080}"; CONFIG_PREFIX=flight-config-dev ;;
  prod) STACK=flight-m1;  SITE_URL="${SITE_URL:?set SITE_URL to the production site, e.g. https://<project>.vercel.app}"; CONFIG_PREFIX=flight-config ;;
  *) echo "usage: infra/deploy.sh dev|prod" >&2; exit 2 ;;
esac

REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
VERSION=$(infra/backend-version.sh)
PACKAGED=$(mktemp -t "$STACK.packaged.XXXXXX.yaml")
trap 'rm -f "$PACKAGED"' EXIT

echo "Deploying $STACK (Env=$ENV, BackendVersion=$VERSION, SiteUrl=$SITE_URL)"
aws cloudformation package --region "$REGION" \
  --template-file infra/flight-m1.yaml \
  --s3-bucket "flight-artifacts-$ACCOUNT_ID" --s3-prefix "$STACK" \
  --output-template-file "$PACKAGED"

aws cloudformation deploy --region "$REGION" \
  --stack-name "$STACK" \
  --template-file "$PACKAGED" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides Env="$ENV" SiteUrl="$SITE_URL" BackendVersion="$VERSION"

# CloudFormation can't put objects in a bucket: upload the routes list on every deploy.
aws s3 cp infra/flight-routes.json "s3://$CONFIG_PREFIX-$ACCOUNT_ID/flight-routes.json" \
  --region "$REGION" --content-type application/json

API=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text)

# Prove the new code is what answers: GET /version must report the version just stamped.
for attempt in 1 2 3 4 5 6; do
  LIVE=$(curl -fsS "$API/version" 2>/dev/null | sed -n 's/.*"backend_version": *"\([^"]*\)".*/\1/p' || true)
  [ "$LIVE" = "$VERSION" ] && break
  sleep 5
done
if [ "$LIVE" != "$VERSION" ]; then
  echo "Deployed $STACK, but $API/version reports '${LIVE:-nothing}', expected $VERSION" >&2
  exit 1
fi
echo "Deployed $STACK: $API (backend $VERSION is live)"
