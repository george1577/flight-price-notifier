# Flight Price Notifier: infrastructure

Everything on AWS is in one CloudFormation template, `flight-m1.yaml` (region
`us-east-1`, no VPC, no SNS). The Lambda code lives in `../lambdas/<function-name>/index.py`;
the template points at those folders and `aws cloudformation package` zips and uploads them.

## Environments and deploys

The same template is deployed twice, so testing never touches real users:

| | prod | dev |
|---|---|---|
| Stack | `flight-m1` | `flight-dev` |
| Names | `subscriptions`, `flight-api`, `flight-parser`, ... | the same, with `-dev` |
| Secrets | `flight/*` | `flight-dev/*` (stage ECPay merchant) |
| Scheduled price check | every 30 min | off (invoke `flight-parser-wrapper-dev` by hand) |
| `SiteUrl` (ECPay return) | `https://flight-price-notifier-bice.vercel.app` | `http://localhost:8080` |
| Deployed by | push to `main` | push to `dev`, or `infra/deploy.sh dev` |
| Frontend | Vercel production (deploy hook, after the backend) | `npm run dev` with `.env.local`, Vercel previews |

**Never rename a prod resource.** CloudFormation replaces a renamed table, queue, bucket or
role, which means a new, empty table. `tests/test_template.py` pins every prod name.

**Keep the API backward compatible** (expand, then switch, then contract). The backend and
the frontend deploy at different moments, so every backend release must still work with the
previous frontend, and the reverse. For example, `POST /subscribe` stays JSON-only, and the
checkout page has its own `POST /checkout`. Add things; remove them in a later release.

### Automatic: GitHub Actions (`.github/workflows/deploy.yml`)

- **Push to `dev`:** tests, then `infra/deploy.sh dev`.
- **Push to `main`:** tests, `infra/deploy.sh prod`, then the Vercel deploy hook for the prod
  frontend. `vercel.json` turns off Vercel's own auto-deploy for `main`, so the frontend can
  never go out before its backend.
- `deploy.sh` fails the run unless `GET /version` reports the version it just deployed.
- Watch a run with `gh run watch`. A green check on the commit means that backend is live.

### By hand: `infra/deploy.sh`

Try backend changes on the dev stack before pushing (needs AWS credentials for the account):

```bash
infra/deploy.sh dev                                                     # flight-dev
SITE_URL=https://flight-price-notifier-bice.vercel.app infra/deploy.sh prod   # flight-m1, rarely
```

### Am I talking to the backend I think I am?

`GET <api>/version` returns `{backend_version, env}`. `infra/backend-version.sh` computes the
same id from your checkout (every `lambdas/flight-*/` folder plus `infra/`, committed or not).
In `npm run dev` a banner appears when they differ, and clears itself once the deploy lands.

### One-time setup (already done for this account)

1. Artifact bucket: `aws s3 mb s3://flight-artifacts-$ACCOUNT_ID --region us-east-1`.
2. GitHub deploy role: deploy `github-deploy-role.yaml` as stack `flight-github-deploy`
   (`--capabilities CAPABILITY_NAMED_IAM`). It trusts only this repo's `dev` and `main`
   branches through GitHub OIDC, so no AWS keys are stored in GitHub.
3. GitHub repository variables: `AWS_DEPLOY_ROLE_ARN` (that stack's output) and
   `PROD_SITE_URL`.
4. GitHub secret `VERCEL_DEPLOY_HOOK_PROD`: create a deploy hook for branch `main` in Vercel
   (Project Settings, Git, Deploy Hooks), then run `gh secret set VERCEL_DEPLOY_HOOK_PROD`.
5. Dev secrets: `flight-dev/ecpay`, `flight-dev/resend`, `flight-dev/travelpayouts` (copies
   of the prod ones; dev always uses ECPay's stage merchant).
6. Frontend API URLs: Vercel's `VITE_FLIGHT_API_URL` is the prod `ApiBaseUrl` for
   Production and the dev one for Preview. Your local `.env.local` uses the dev one.

Read a stack's API URL:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name flight-dev \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text
```

## Things CloudFormation does not do

- **Existing resources.** The template creates the `subscriptions` and
  `notification_history` tables and `flight-lambda-role` by name. If you already
  made any of them by hand, delete them first (or
  [import them](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import.html))
  or the stack create fails with "already exists". The tables have
  `DeletionPolicy: Retain`, so deleting the stack keeps your data.
- **Routes config object.** The stack creates the `flight-config-<account>` bucket
  (`flight-config-dev-<account>` for dev), but CloudFormation can't put objects in it.
  `infra/deploy.sh` uploads the committed list on every deploy. By hand:

  ```bash
  aws s3 cp infra/flight-routes.json s3://flight-config-$ACCOUNT_ID/flight-routes.json \
    --region us-east-1 --content-type application/json
  ```

  Until it's uploaded, the `flight-price-check` rule's runs fail with `NoSuchKey`.
  To add a route, edit `infra/flight-routes.json` and upload it again (the scheduled
  check needs no code change, but see "Deliberate duplication" below for the other
  four places a new route must be added). To change how often it runs, change `ScheduleExpression` on
  `FlightPriceCheckRule` and redeploy.

- **Secrets.** `flight/*` secrets (`flight/travelpayouts`, `flight/resend`, `flight/ecpay`)
  are created in the prerequisites, not here; the dev stack reads `flight-dev/*` copies. Each
  stack's role is only granted read access to its own prefix.
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

The route list lives in five places that **must change together** when you add a route:

1. `src/lib/plans.ts` (the dashboard cards)
2. `PLANS` in `lambdas/flight-save-subscription/index.py` (which `plan_name`s POST /subscribe accepts)
3. `infra/flight-routes.json` (what the scheduled check fetches; re-upload it to S3)
4. `CITY_NAMES` in `lambdas/flight-fare-notification/index.py` (city names in the price alert)
5. `CITY_NAMES` in `lambdas/flight-status-notification/index.py` (city names in the welcome / cancel email)

`lambdas/tests/test_route_tables.py` fails if any of these five drift apart.

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

A synthetic `ReturnURL` callback (the activation path, without paying). It signs an
ECPay-shaped body, empty `CustomField3`/`CustomField4` included, with the public stage keys:

```bash
TRADE_NO=<the row's merchant_trade_no>
BODY=$(python3 - "$TRADE_NO" <<'EOF'
import hashlib, sys, urllib.parse
f = {"MerchantID": "3002607", "MerchantTradeNo": sys.argv[1], "RtnCode": "1", "RtnMsg": "交易成功",
     "SimulatePaid": "0", "CustomField1": "you@example.com", "CustomField2": "TPE-TYO",
     "CustomField3": "", "CustomField4": ""}
raw = "HashKey=pwFHCqoQZGmho4w6&" + "&".join(f"{k}={f[k]}" for k in sorted(f, key=str.lower)) + "&HashIV=EkRm7iFT261dpevs"
enc = urllib.parse.quote_plus(raw).replace("~", "%7E").lower()
for a, b in (("%2d", "-"), ("%5f", "_"), ("%2e", "."), ("%21", "!"), ("%2a", "*"), ("%28", "("), ("%29", ")")):
    enc = enc.replace(a, b)
f["CheckMacValue"] = hashlib.sha256(enc.encode()).hexdigest().upper()
print(urllib.parse.urlencode(f))
EOF
)
curl -s -X POST "$API/ecpay-return" -d "$BODY"; echo     # 1|OK
curl -s -X POST "$API/ecpay-return" -d "${BODY}x"; echo  # 0|CheckMacValueInvalid
```

The row is now `subscription_status=active` with `activated_at`, `current_period_end`
(one calendar month on) and `current_period_end_date`. Posting the same body again
replies `1|OK` and changes nothing. `POST /subscribe` on the active row now answers JSON
(target updated in place), not the cashier form.

Stage payment (manual): open the dashboard, click 開始追蹤, pay with the test card
`4311-9522-2222-2222`, `12/30`, CVV `222`, OTP `1234`. You should land on
`/app?purchase=success` (no 405) and the card should turn 已訂閱 (有效) within a few
seconds; the row is `active` with `merchant_trade_no` and `current_period_end` set. In the
stage back office (`vendor-stage.ecpay.com.tw`, 信用卡收單 → 定期定額查詢), filter by the
row's `merchant_trade_no` to find the order.

Renewals (manual, stage). ECPay posts each charge from the second on to
`POST /ecpay-period` (`flight-ecpay-period`). To see one without waiting a month, switch
the billing period to daily with two charges, pay, check the next day, then switch back:

```bash
aws lambda update-function-configuration --region us-east-1 --function-name flight-save-subscription \
  --environment "Variables={API_BASE_URL=$API,PERIOD_TYPE=D,FREQUENCY=1,EXEC_TIMES=2}"
# pay the first period on the dashboard with the test card, then the next day:
aws logs tail /aws/lambda/flight-ecpay-period --region us-east-1 --since 1d
```

Expect a `PeriodReturnURL` log line with the whole payload (minus `CheckMacValue`) showing
`RtnCode=1` and `TotalSuccessTimes=2`, a `renewed` line, and the row's
`current_period_end` one day later than before. Because 2 of `ExecTimes=2` is the last
charge, the row is now `cancelled`, and the parser expires it when that day ends.
Afterwards, redeploy the stack (or set `PERIOD_TYPE=M,FREQUENCY=1,EXEC_TIMES=999` the
same way) to go back to monthly. A declined renewal only increments `failed_charge_count`.
The row turns `expired` on the 6th consecutive decline, which is when ECPay ends the series.

```bash
curl -s "$API/subscriptions?email=you@example.com"
```

Expect a JSON array with that row and `"target_price": 10000` (a number, not a string).
Note: the API trusts the email the client sends (no JWT check); this gap is accepted for M1.

Cancel (#21), on an `active` row:

```bash
curl -s -X POST "$API/cancel" -H 'content-type: application/json' \
  -d '{"email":"you@example.com","route":"TPE-TYO"}'
aws logs tail /aws/lambda/flight-cancel-subscription --region us-east-1 --since 5m
```

Expect JSON with `subscription_status=cancelled`, `cancelled_at`, and the same
`current_period_end` as before. The log line `ecpay_period_action` holds ECPay's reply:
`RtnCode=1` for a real paid order, `90100150` (不存在的訂單編號) for a self-signed one; both
cancel locally. A second POST returns the row unchanged and sends no second email; a
`pending_payment`/`expired` row gets 409, no row 404, and an unreachable ECPay 502 (nothing
changed). Until `current_period_end` the row still gets alerts and `/subscribe` updates its
target in place; after it, the parser moves it to `expired` and `/subscribe` returns the
cashier form again. For a real test-card order, the stage back office (定期定額查詢) should
show it as terminated.

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

### flight-status-notification (welcome / cancel email, M2)

`flight-ecpay-return` puts one `{"event_type":"welcome","email","route","plan_name","merchant_trade_no"}`
message on `flight-status-queue` per successful activation. ECPay resends and
`SimulatePaid=1` enqueue nothing. `flight-status-notification` (BatchSize 1) emails it
with the same Resend rules as the fare alert. The NT$ monthly price comes from
`flight/ecpay`'s `amount`, and both secrets are cached per warm container. `cancel`
messages (route + `current_period_end_date`, shown as 有效至, + `cancelled_at`) use the
same consumer. SQS can deliver a message twice, so after a send the consumer records the
trade number (`welcome_sent_for`) or `cancelled_at` (`cancel_email_sent_for`) on the
subscription row and skips a redelivery that matches. A message without those fields is
always sent.

Smoke check without a card: send a synthetic welcome addressed to your Resend-account email.

```bash
SQURL=$(aws sqs get-queue-url --region us-east-1 --queue-name flight-status-queue --query QueueUrl --output text)
aws sqs send-message --region us-east-1 --queue-url "$SQURL" \
  --message-body '{"event_type":"welcome","email":"you@example.com","route":"TPE-TYO","plan_name":"tokyo"}'
aws logs tail /aws/lambda/flight-status-notification --region us-east-1 --since 5m
```

Expect one email, `✈️ 歡迎訂閱 台北 → 東京 機票降價通知`. The full check is one real or
self-signed `/ecpay-return` activation: it sends exactly one welcome, and re-POSTing
the same callback sends none.

## Tests

The Lambdas are tested locally with pytest + moto (no AWS account needed):

```bash
cd lambdas && uv run pytest
```
