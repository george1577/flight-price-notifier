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
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides SiteUrl=https://<your-site>.vercel.app
```

`SiteUrl` is the public URL of the SPA (no trailing slash). `flight-ecpay-result`
redirects the browser to `<SiteUrl>/app?purchase=...` after ECPay's cashier. It is
required on the first deploy; later deploys keep the previous value unless you pass it again.

**M2 release order:** create `flight/ecpay` (below), deploy the stack, then release the
front end in the same release. From this deploy on, `POST /subscribe` answers with an
HTML checkout page instead of JSON, and the old client breaks on it.

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
- **Routes config object.** The stack creates the `flight-config-<account>` bucket but
  CloudFormation can't put objects in it. After every deploy that changes the routes
  (and once after the first deploy), upload the committed list:

  ```bash
  aws s3 cp infra/flight-routes.json s3://flight-config-$ACCOUNT_ID/flight-routes.json \
    --region us-east-1 --content-type application/json
  ```

  Until it's uploaded, the `flight-price-check` rule's runs fail with `NoSuchKey`.
  To add a route, edit `infra/flight-routes.json` and upload it again (the scheduled
  check needs no code change, but see "Deliberate duplication" below for the other
  three places a new route must be added). To change how often it runs, change `ScheduleExpression` on
  `FlightPriceCheckRule` and redeploy.

- **Secrets.** `flight/*` secrets (`flight/travelpayouts`, `flight/resend`, `flight/ecpay`)
  are created in the prerequisites, not here. The role is only granted read access to them.
- **`flight/ecpay` (M2).** Create it before deploying M2, or `POST /subscribe` fails. M2
  runs on ECPay's public stage test merchant:

  ```bash
  aws secretsmanager create-secret --region us-east-1 --name flight/ecpay \
    --secret-string '{"merchant_id":"3002607","hash_key":"pwFHCqoQZGmho4w6","hash_iv":"EkRm7iFT261dpevs","env":"stage","amount":"300"}'
  ```

  `amount` is the monthly price in whole TWD; it becomes both `TotalAmount` and
  `PeriodAmount` on the checkout form, so changing the price is a secret update, not a
  code change. `env` picks the ECPay host; only `stage` (`payment-stage.ecpay.com.tw`)
  exists until M3. `flight-save-subscription` caches the secret per warm container, so
  after a `put-secret-value` force a cold start (any env-var change) before testing.
- **Region.** The template's `Rules` block rejects any region other than `us-east-1`,
  so a deploy elsewhere fails at validation, before anything is created.
- **Routes object access.** `flight-lambda-role` may only `s3:GetObject` the single key
  `flight-config-<account>/flight-routes.json`. Nothing in the stack writes to the bucket;
  you upload the file with your own credentials (above).

## Deliberate duplication: adding a route

Each Lambda is a single deployable `index.py` (no shared layer), and spec #5 keeps the
email renderer inline in `flight-fare-notification`. Small helpers (logging, TWD
formatting, route/city lookups) are therefore duplicated across Lambdas on purpose; do
not factor them into a shared module.

The route list lives in four places that **must change together** when you add a route:

1. `src/lib/plans.ts` (the dashboard cards)
2. `PLANS` in `lambdas/flight-save-subscription/index.py` (which `plan_name`s POST /subscribe accepts)
3. `infra/flight-routes.json` (what the scheduled check fetches; re-upload it to S3)
4. `CITY_NAMES` in `lambdas/flight-fare-notification/index.py` (city names in the email)

`lambdas/tests/test_route_tables.py` fails if these four drift apart.

The ECPay CheckMacValue helper (`check_mac_value`) is duplicated the same way into every
Lambda that signs or verifies ECPay requests. When you copy it into a new Lambda, add the
Lambda's directory to `CMV_LAMBDAS` in `lambdas/tests/test_checkmacvalue.py`, which runs
every copy against ECPay's official known-answer vectors
(`lambdas/tests/fixtures/checkmacvalue.json`).

## Smoke check after deploy

```bash
API=<ApiBaseUrl>
curl -s -X POST "$API/subscribe" -H 'content-type: application/json' \
  -d '{"email":"you@example.com","plan_name":"tokyo","target_price":10000}'
aws dynamodb get-item --region us-east-1 --table-name subscriptions \
  --key '{"email":{"S":"you@example.com"},"route":{"S":"TPE-TYO"}}'
```

Expect the POST to print an HTML page whose `<form>` posts to
`https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5` with a `CheckMacValue`
hidden input, and the row to have `route=TPE-TYO`, `currency=TWD`, `target_price=10000`,
`subscription_status=pending_payment` and a 20-character `merchant_trade_no` that
matches the form's `MerchantTradeNo`. The row stays `pending_payment` until ECPay's
`ReturnURL` callback activates it.

The browser-return redirect:

```bash
curl -si -X POST "$API/ecpay-result" -d 'RtnCode=1' | grep -i '^location'   # <SiteUrl>/app?purchase=success
curl -si -X POST "$API/ecpay-result" -d 'RtnCode=0' | grep -i '^location'   # <SiteUrl>/app?purchase=failed
```

Stage payment (manual): open the dashboard, click 開始追蹤, pay with the test card
`4311-9522-2222-2222`, `12/30`, CVV `222`, OTP `1234`. You should land on
`/app?purchase=success` (no 405). In the stage back office
(`vendor-stage.ecpay.com.tw`, 信用卡收單 → 定期定額查詢), filter by the row's
`merchant_trade_no` to find the order.

```bash
curl -s "$API/subscriptions?email=you@example.com"
```

Expect a JSON array with that row and `"target_price": 10000` (a number, not a string).
Note: the API trusts the email the client sends (no JWT check); this gap is accepted for M1.

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

### flight-parser-wrapper (every route, every 30 minutes)

Invoke the wrapper once by hand, then check that the parser logs show both routes:

```bash
aws lambda invoke --region us-east-1 --function-name flight-parser-wrapper /tmp/wrapper.json
cat /tmp/wrapper.json   # {"ok": true, "invoked": 2, "routes": ["TPE-TYO", "TPE-SEL"]}
aws logs tail /aws/lambda/flight-parser --region us-east-1 --since 5m | grep -E 'TPE-(TYO|SEL)'
```

### flight-fare-notification (the email)

The template wires `flight-fare-queue` to `flight-fare-notification` with an
event-source mapping (`FareNotificationQueueMapping`, BatchSize 1). The moment that
mapping exists it drains the queue, so **purge the queue before the deploy that first
creates the mapping**. Otherwise stale test matches (e.g. `test@example.com` from the
parser smoke check) all send at once, the demo sender rejects them with 403s, and the
burst can push live sends into Resend 429s. On a brand-new stack the queue starts empty,
so this only matters if the stack was deployed before the mapping was added:

```bash
QURL=$(aws sqs get-queue-url --region us-east-1 --queue-name flight-fare-queue --query QueueUrl --output text)
aws sqs purge-queue --region us-east-1 --queue-url "$QURL"
# purge takes up to 60s; then run the package + deploy commands above
```

Keep `FlightFareQueue.VisibilityTimeout` (180s) strictly greater than the function's
`Timeout` (30s), about 6x. Change both together.

The Lambda reads `{"api_key","from"}` from `flight/resend` (`from` stays
`onboarding@resend.dev` until M3) and caches it per warm container, so after a
`put-secret-value` force a cold start (any env-var change) before testing. The demo
sender only delivers to your own Resend-account email, so test with a subscriber whose
email is that address. If `flight/travelpayouts` has a `marker`, the booking link gets
`?marker=<id>`; without it the link is plain.

Smoke check: subscribe your Resend-account email with a target above the live fare,
invoke the parser (see above), then:

```bash
aws logs tail /aws/lambda/flight-fare-notification --region us-east-1 --since 10m
aws dynamodb query --region us-east-1 --table-name notification_history \
  --key-condition-expression 'pk = :p' \
  --expression-attribute-values '{":p":{"S":"you@example.com#TPE-TYO"}}' --no-scan-index-forward
```

Expect one email (subject `✈️ 台北 → 東京 降價通知！NT$… 已達標`) and one history row.
A 403/422 from Resend is logged and dropped (no row); a 429/5xx raises and SQS redelivers.

## Tests

The Lambdas are tested locally with pytest + moto (no AWS account needed):

```bash
cd lambdas && uv run pytest
```
