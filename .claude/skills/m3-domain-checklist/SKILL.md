---
name: m3-domain-checklist
description: Flight Price Notifier Milestone 3 GO-LIVE checklist — verifies the things M3 actually changes: the custom domain serves the site over HTTPS, the Resend email sender uses the domain (reaching a non-owner inbox, proving the M2 sandbox limit is lifted), and the secrets are consistent. Use when the student says "驗收 M3", "上線檢查", "go-live checklist", or after `m3-domain`.
---

# M3 — Go-Live Checklist（上線檢查清單）

## What this skill does

Confirms the things M3 actually changes are live: **(1) the site is on your custom domain**, **(2) email is sent from your domain and reaches anyone** (not just your own inbox), and **(3) the secrets are consistent**. Reports a clear ✅/⚠️/❌ per item.

> **Scope note.** This checklist is deliberately scoped to the **domain + email cutover** — the things M3 introduces. It does **not** re-run the payment journey or grep the front-end bundle: M3 doesn't touch the payment path (the ECPay callbacks stay on the API Gateway URL and are hit server-to-server), so payments are an M2 concern ([[m2-ecpay-subscription-checklist]]), and the front-end secret-hygiene check belongs to general launch prep. Run those separately if you need a full launch audit.

## Architecture

![Flight Fare / Notification architecture (M3) — this checklist verifies the custom-domain cutover on the full M2 system. The Product Site is now labeled [domain].com (Vercel host) instead of *.vercel.app — that relabel IS what M3 changes. The Product Site POSTs to the ECPay Lambda Handlers (flight-ecpay-return / flight-ecpay-period / flight-cancel-subscription), which write subscription_status onto Subscriptions [DynamoDB]; a "subscription check" gate on that table makes the Parser scan only active rows. On payment events the handlers enqueue to the Notification-side SQS, where the Subscription Status Notification Lambda emails welcome/cancel via Resend — now from alerts@[domain].com, which lifts the M2 sandbox limit so it reaches any inbox (Section B verifies exactly this). The notifier flow: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the travelpayouts API, scans Subscriptions, enqueues matches to the Flight Fare Notification SQS → Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and emails via Resend. Inset: the subscribe → ECPay → callback (W = write) loop. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight_notification_structure2.jpg)

## Execution mode

CLI uses `curl`/`aws`; Cowork uses dashboards/MCP. `aws` commands `--region us-east-1`.

## How to run

Ask the student for: their custom domain (or subdomain), the API id, and a **non-owner** test inbox. Then run each section and report.

### Section A — Site on the custom domain
- **A1** The domain serves the app over HTTPS with a valid cert:
  ```bash
  curl -sSI https://yourdomain.com | head -1          # HTTP/2 200
  ```
  (If you bound a subdomain like `fly.yourdomain.com`, check that exact host. A bad/missing cert makes `curl` error out — so a clean `200` also confirms TLS. DNS can take minutes to propagate; a first-try failure may just be propagation — wait and retry.)
  > **Cowork: the sandbox `curl` is proxy-blocked** (outbound HTTPS to arbitrary hosts returns a 403/000 — [[resend-best-practice]] Rule 0 / [[aws-best-practice]] constraint #2). Don't conclude the site is down from a sandbox `curl`. Verify the `200` with a **URL-fetch tool** instead — e.g. the **Vercel URL-fetch MCP** (`web_fetch_vercel_url` / `get_access_to_vercel_url`) — which fetches the live host from outside the sandbox. The `curl` line is for CLI mode.
  > **Don't trust Vercel `get_project`'s `domains` array for the attach check — it lags.** The project's domains list can still show the host as pending/absent for a while after it's actually live (and conversely keep a stale entry). **Confirm the domain is attached by *fetching the host* and getting a `200`** (above), not by reading the array. The fetch is ground truth; the API field is eventually-consistent.

### Section B — Email sender on your domain
- **B1** Resend shows the sending domain (or sending subdomain) **Verified** (SPF + DKIM). *(If it still says "not verified" with the DNS records in place, the student likely hasn't pressed Resend's **Verify Domain** button — it isn't automatic; tell them to click it in Resend → Domains. See [[m3-domain]] Step 3.)*
- **B2** Secret `from` is your domain:
  ```bash
  aws secretsmanager get-secret-value --secret-id flight/resend --region us-east-1 \
    --query SecretString --output text | grep -o 'alerts@[^"]*'
  ```
- **B3** **Non-owner delivery (the sandbox-lift proof):** a real send to an inbox that is **NOT** your Resend-account email must arrive, from `alerts@<your domain>`. This one test covers both "sends from your domain" and "reaches real users."

  **Cowork recipe** (you can't `curl api.resend.com` from the sandbox — send from the Lambda; [[resend-best-practice]] Rule 0):
  1. **Bust the cache first** if you just changed `flight/resend` — else you get a stale-`from` `403` (Step 3.5 / [[resend-best-practice]] Rule 5a):
     ```bash
     aws lambda update-function-configuration --function-name flight-fare-notification \
       --environment "Variables={CACHE_BUST=$(date +%s)}" --region us-east-1
     ```
  2. **Invoke with the REAL event shape** — `flight-fare-notification` reads an SQS-style body and needs **`target_price`** *and* `cheapest` (missing either → `KeyError: 'target_price'` / `'cheapest'`). Pick a `route` the test recipient is **NOT** subscribed to (see note below). The AWS MCP `lambda invoke` is **raw-input** (do **not** base64 the payload; and **don't** add `--query`/`--log-type` — the MCP errors on the streaming `Payload` even though the function still runs):
     ```bash
     aws lambda invoke --function-name flight-fare-notification --region us-east-1 \
       --payload '{"Records":[{"body":"{\"email\":\"<non-owner>@example.com\",\"route\":\"TPE-TYO\",\"target_price\":10000,\"cheapest\":{\"price\":9531,\"depart_date\":\"2026-07-15\"},\"cheapest_usd\":{\"price\":295}}"}]}' \
       /tmp/out.json
     ```
     > **Use a route the recipient is NOT subscribed to.** This synthetic send writes a `notification_history` dedup row for `<email>#<route>`. If that pair matches a *real* subscription, the row **suppresses the recipient's genuine alert for 24h**. Picking an unsubscribed route (or a throwaway non-owner email) avoids collateral, and you still delete the row in step 4 either way.
  3. **Verify by reading CloudWatch logs** (not the invoke output — the MCP can't read that file). Look for `RESEND_OK 200 → <non-owner>`:
     ```bash
     aws logs filter-log-events --log-group-name /aws/lambda/flight-fare-notification \
       --query "events[].message" --region us-east-1
     ```
     A `403 validation_error` here → the domain isn't actually Verified (fix B1), or you skipped the cache-bust (step 1).
  4. **Clean up the dedup row** so a real future alert isn't suppressed for 24h — delete the `notification_history` row this test wrote (`pk = "<email>#<route>"`, range key `sent_at`):
     ```bash
     aws dynamodb delete-item --table-name notification_history --region us-east-1 \
       --key '{"pk":{"S":"<non-owner>@example.com#TPE-TYO"},"sent_at":{"S":"<the sent_at it wrote>"}}'
     ```

### Section D — Config
- **D3** **Secrets consistent** for the chosen mode:
  - `flight/resend` — `from` is your domain (overlaps B2) and `api_key` is set.
  - `flight/ecpay` — `merchant_id`/`hash_key`/`hash_iv` are all **stage** *or* all **prod** (never mixed), and `env` matches the cashier host (`stage` → `payment-stage…`, `prod` → `payment.ecpay.com.tw`). Staying on the stage merchant is fine — just confirm it's internally consistent. (Switching to a real merchant is the optional [[ecpay-go-live]].)
  ```bash
  aws secretsmanager get-secret-value --secret-id flight/ecpay --region us-east-1 \
    --query SecretString --output text
  ```

## Reporting

| Item | Status | Notes |
|---|---|---|
| A1 site serves HTTPS 200 on the domain | ✅/❌ | |
| B1 Resend domain Verified | ✅/❌ | SPF + DKIM |
| B2 `from` = your domain | ✅/❌ | |
| B3 reaches a non-owner inbox | ✅/❌ | sandbox-lift proof |
| D3 secrets consistent | ✅/❌ | resend `from` + ecpay all-stage/all-prod |

**Verdict:**
- All ✅ → 「🎉 網域與寄信都掛在你自己的品牌上、API 也認得新網域，M3 完成！」（想再加亮點：『啟動 M4』自架一台真的 OpenClaw 當 24 小時 AI 機票客服。）
- Any ❌ → fix and re-run `上線檢查`: B1 not Verified → check the SPF/DKIM DNS records; B3 `403` → domain not actually Verified (B1); D3 mixed stage/prod → align `merchant_id`/keys/`env` to one mode.
