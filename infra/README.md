# Flight Price Notifier: M1 infrastructure

Everything M1 needs on AWS is in one CloudFormation template, `flight-m1.yaml`
(region `us-east-1`, no VPC, no SNS). The Lambda code lives in
`../lambdas/<function-name>/index.py`; the template points at those folders and
`aws cloudformation package` zips and uploads them.

## Deploy

One-time: an S3 bucket that holds the packaged Lambda zips.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 mb s3://flight-artifacts-$ACCOUNT_ID --region us-east-1
```

Each deploy (run from the repo root):

```bash
aws cloudformation package --region us-east-1 \
  --template-file infra/flight-m1.yaml \
  --s3-bucket flight-artifacts-$ACCOUNT_ID \
  --output-template-file /tmp/flight-m1.packaged.yaml

aws cloudformation deploy --region us-east-1 \
  --stack-name flight-m1 \
  --template-file /tmp/flight-m1.packaged.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```

Then read the API URL and give it to the frontend:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name flight-m1 \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text
```

Set it as `VITE_FLIGHT_API_URL` (no trailing slash) in Vercel's project env vars and
in your local `.env.local`, then redeploy the site. See `../.env.example`.

## Things CloudFormation does not do

- **Existing resources.** The template creates the `subscriptions` and
  `notification_history` tables and `flight-lambda-role` by name. If you already
  made any of them by hand, delete them first (or
  [import them](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import.html))
  or the stack create fails with "already exists". The tables have
  `DeletionPolicy: Retain`, so deleting the stack keeps your data.
- **Secrets.** `flight/*` secrets (`flight/travelpayouts`, `flight/resend`, ...) are
  created in the prerequisites, not here. The role is only granted read access to them.

## Smoke check after deploy

```bash
API=<ApiBaseUrl>
curl -s -X POST "$API/subscribe" -H 'content-type: application/json' \
  -d '{"email":"you@example.com","plan_name":"tokyo","target_price":10000}'
aws dynamodb get-item --region us-east-1 --table-name subscriptions \
  --key '{"email":{"S":"you@example.com"},"route":{"S":"TPE-TYO"}}'
```

Expect `route=TPE-TYO`, `currency=TWD`, `target_price=10000`, no `subscription_status`.

### flight-parser (one route)

Give the test subscriber a target above the live fare, invoke the parser for its
route, then check the queue depth went up and the logs show the cheapest fare and the
match count:

```bash
aws lambda invoke --region us-east-1 --function-name flight-parser \
  --cli-binary-format raw-in-base64-out \
  --payload '{"origin":"TPE","destination":"TYO","route":"TPE-TYO"}' /tmp/parser.json
QURL=$(aws sqs get-queue-url --region us-east-1 --queue-name flight-fare-queue --query QueueUrl --output text)
aws sqs get-queue-attributes --region us-east-1 --queue-url "$QURL" \
  --attribute-names ApproximateNumberOfMessages
```

An empty or 429 TWD answer from Travelpayouts logs "no TWD fare" and enqueues nothing.
That is a skip, not a failure. Messages you enqueue while testing stay on the queue: **purge
`flight-fare-queue` before the notifier's event-source mapping is created** so stale test
messages don't all send at once (`aws sqs purge-queue --queue-url "$QURL"`).

## Tests

The Lambdas are tested locally with pytest + moto (no AWS account needed):

```bash
cd lambdas && uv run pytest
```
