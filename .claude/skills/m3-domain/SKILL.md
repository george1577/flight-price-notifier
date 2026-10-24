---
name: m3-domain
description: Flight Price Notifier Milestone 3 — bind your own custom domain to BOTH the Vercel front-end AND the Resend email sender (one domain, two DNS setups). This skill does the SETUP only; all verification (and the optional ECPay production switch) happen elsewhere. Use when the student says "啟動 M3", "start M3", "綁網域", "上線", "go live", or "正式開張".
---

# M3 — Custom Domain（把網站和寄信都掛上你自己的網域）

## What this skill does

Takes the working paid product and gives it **your own brand**. You buy **one domain** and set it up in **two** places (two separate DNS setups at the same registrar):

1. **The website** — `yourdomain.com` → your Vercel front-end (`A`/`CNAME` records). Visitors load `https://yourdomain.com` instead of `*.vercel.app`.
2. **The email sender** — `alerts@yourdomain.com` as the Resend `From:` (SPF + DKIM records). Emails come from your domain (inbox, not spam) — **and this lifts the M2 Resend-sandbox limit**: once the domain is verified, Resend delivers to **anyone**, not just your own account email.

> **Scope:** this skill performs the **setup actions only**. **Verification is not done here** — run **[[m3-domain-checklist]]** to confirm everything works (HTTPS, deliverability, non-owner delivery, etc.). And the **optional ECPay production switch** (real MerchantID) is its own skill: **[[ecpay-go-live]]**.

> **The domain covers the website + email; it does NOT have to cover the ECPay callbacks.** ECPay's `ReturnURL`/`PeriodReturnURL`/`OrderResultURL` can stay on the raw **API Gateway** URL (`*.execute-api…`) — ECPay only needs a public HTTPS endpoint on port 80/443, and users never see those URLs. Mapping a custom domain onto API Gateway (ACM cert + API mapping) is **extra, optional** work; don't think you must.

Why this matters: 有了自己的網域，才是真正屬於你的產品 — 才能打廣告、做 SEO、累積品牌資產。

End state of this skill: the Vercel domain + the Resend sending domain are **set up** (records added, secret flipped). You then **verify** with the checklist.

## Architecture (where M3 fits)

![Flight Fare / Notification architecture (M3) — the full M2 system with the custom domain bound. The Product Site is now labeled [domain].com (Vercel host) instead of *.vercel.app — that relabel IS the M3 change. The Product Site POSTs to the ECPay Lambda Handlers (flight-ecpay-return / flight-ecpay-period / flight-cancel-subscription), which talk to ECPay and write subscription_status onto Subscriptions [DynamoDB]; a "subscription check" gate on that table makes the Parser scan only active rows. On payment events the handlers enqueue to the Notification-side SQS, where the Subscription Status Notification Lambda emails welcome/cancel via Resend (now from alerts@[domain].com). The notifier flow: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the 3rd-party travelpayouts API, scans Subscriptions, and enqueues matches to the Flight Fare Notification SQS → Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and emails via Resend. Inset: the subscribe → ECPay → callback (W = write) loop that flips subscription_status. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight_notification_structure2.jpg)

M3 changes only the **outer edges** of this diagram, not the flow:
- **`[domain].com` Product Site** (top-left) — Step 2 binds your custom domain to the Vercel-hosted front-end (instead of `*.vercel.app`).
- **Email [Resend]** (right) — Step 3 makes both the *Subscription Status Notification* and *Flight Fare Notification* emails send **from your domain** (`alerts@…`), lifting the M2 sandbox limit.
- Everything in between is **unchanged**: the **ECPay Lambda Handlers** (`flight-ecpay-return` / `-period` / `-cancel-subscription`), the `subscription check`, the SQS fan-out, the parser, and `Notification History` all keep running exactly as M2 built them. M3 touches no Lambda code and no payment path.

## When to load this skill

- "啟動 M3" / "start M3" / "綁網域" / "上線" / "go live" / "正式開張"

Requires M2 done (`m2-ecpay-subscription-checklist` green). Adds one account: a **domain registrar** (Namecheap / Cloudflare / GoDaddy — any). The domain string comes from the student — see `m3-domain-prerequisites` (it's not discoverable from AWS).

## Execution mode

DNS + dashboards mostly (Vercel, Resend, registrar). `vercel` CLI / MCP for domain attach; `aws` only to update the `flight/resend` secret. `aws` commands `--region us-east-1`.

## Required external accounts (new)

| # | Service | Used for |
|---|---|---|
| 9 | Domain registrar (any) | the domain you'll bind (`yourdomain.com`) |

## Conversational flow

### Step 1 — Confirm the host(s) and where DNS lives

> **Default to a SUBDOMAIN, not the apex — and don't ask.** Bind a dedicated subdomain for both the site (`fly.yourdomain.com`) and email (`mail.yourdomain.com`) **by default**, even when the apex is free. Reasons: it never collides with another project on the same domain, it sidesteps the apex-`A`-record and the "verify the same apex in two accounts" problems (Step 3), and — most importantly — **it avoids forcing the student to re-verify a domain they may already have set up, which delays the build.** Pick sensible subdomains and proceed; only fall back to the apex if the student explicitly asks for a bare `yourdomain.com`.

From `m3-domain-prerequisites` you already know **where the DNS is** (a **Route 53** hosted zone you edit via the AWS MCP, or an external registrar). Carry that forward and use the subdomain hosts above.

> **DNS edits in Route 53:** every record below is an **`UPSERT`** via `aws route53 change-resource-record-sets --hosted-zone-id <id> --change-batch '{…}'` — never a blind create that could clobber an existing record. **Multi-value records (TXT) must include ALL existing values plus the new one** in a single `UPSERT` (Route 53 replaces the whole record set). If the zone is in a different AWS account than the flight project, switch the `[default]` profile to the **domain account** for these steps.

### Step 2 — Attach the host to Vercel

⚠️ **Cowork: adding the domain to the project is a MANUAL dashboard step** — there's no Vercel-domain MCP tool. The student does: **Vercel dashboard → Project → Settings → Domains → Add** `<your host>`. (CLI users can `vercel domains add <host>`.)

Then Vercel **displays the exact DNS records to create** — read them off the dashboard and create them in DNS. With the **subdomain default** (Step 1), this is a single `CNAME`; the records also depend on **whether the host was ever linked to another Vercel account**:

- **Subdomain (default)** → a `CNAME`. ⚠️ **Use the EXACT CNAME target Vercel gives you** — for a previously-linked host it's often a **project-specific** target like `…vercel-dns-017.com`, **not** the generic `cname.vercel-dns.com`. (Only if the student insisted on the **apex** → an `A` record to Vercel's anycast IP, which Vercel shows.)
- **If the host was linked elsewhere before**, Vercel also demands a **`_vercel` TXT** ownership record (`vc-domain-verify=…`). ⚠️ **`_vercel` TXT is multi-value** — if it already holds a verify token for another subdomain, you must **append** the new value, not overwrite (in Route 53, `UPSERT` the `_vercel` TXT with **both** values).

After the records propagate, Vercel shows the domain **Valid/active** and issues a TLS cert automatically.

### Step 3 — Set up your Resend sending domain (use a subdomain by default)

⚠️ **You CANNOT verify the same apex in two Resend accounts.** Resend's DKIM selector is `resend._domainkey.<apex>` — if that apex is already verified in **another** Resend account (e.g. a different project of yours), the selector **collides** and verification fails. **This is exactly why Step 1 defaults to a dedicated sending subdomain** — use **`mail.yourdomain.com`**, don't ask, and you avoid the collision entirely.

1. Resend → **Domains → Add Domain**. Enter **`mail.yourdomain.com`** (the default sending subdomain from Step 1).
2. Add the **exact** records Resend displays. For a sending subdomain `send.<sub>` they're typically:
   - **DKIM** — `TXT` at `resend._domainkey.<sub>` (the long `p=…` key)
   - **MX** — `send.<sub>` → `10 feedback-smtp.<region>.amazonses.com`
   - **SPF** — `TXT` at `send.<sub>` → `v=spf1 include:amazonses.com ~all`
   - (optionally a DMARC `TXT` at `_dmarc.<sub>`)
   Create them in DNS (Route 53 `UPSERT`, or the registrar).
3. **⚠️ Tell the student (imperatively): open `resend.com` → Domains → `fly.<domain>` → click the「Verify Domain」button, then report back.** Resend does **not** auto-verify, and **the agent cannot read or trigger Resend's status — there is no Resend MCP/CLI tool.** So you can't poll it: after the DNS records are in, the student must click Verify themselves (re-click after a minute if DNS hasn't propagated) and tell you they did. The build stalls at "not verified" until they do.
   > **Verification signal you CAN see (don't wait on the dashboard):** since you can't read Resend's "Verified" badge, treat the **first real send** as the proof — a **`200` from the Resend POST is the de-facto "Verified"** (Step 3.5 sends one). A **`403 validation_error` means not-yet-verified** (or a stale-`from` cache — Step 5). So: have the student click Verify, then just **send** and read the status code; don't block on a dashboard state the agent can't observe.
4. **Only after Verified**, flip the secret to send from your (sub)domain — using the **project account's** profile if multi-account:
```bash
aws secretsmanager put-secret-value --secret-id flight/resend \
  --secret-string '{"api_key":"re_...","from":"alerts@mail.yourdomain.com"}' \
  --region us-east-1
```
5. **⚠️ Bust the warm-container cache before testing.** The sending Lambda (`flight-fare-notification`) reads `flight/resend` **at container init and caches it** — after `put-secret-value`, a warm container keeps the **old `from`** and you'll get Resend `403 validation_error` (sandbox) even though the secret is already correct. Force a cold start:
```bash
aws lambda update-function-configuration --function-name flight-fare-notification \
  --environment "Variables={CACHE_BUST=$(date +%s)}" --region us-east-1
```
(Any env-var change forces a fresh container that re-reads the secret. Same applies to `flight-status-notification` if it reads `flight/resend`.)
6. **Smoke-send once → the `200`/`403` is your verification signal** (you can't read Resend's dashboard). Invoke the sender with a **real SQS-shaped event** — `flight-fare-notification` reads `msg["target_price"]` (and `cheapest`), so **include `target_price`** or it throws `KeyError: 'target_price'`:
```bash
aws lambda invoke --function-name flight-fare-notification --region us-east-1 \
  --payload '{"Records":[{"body":"{\"email\":\"<non-owner>@example.com\",\"route\":\"TPE-TYO\",\"target_price\":10000,\"cheapest\":{\"price\":9531,\"depart_date\":\"2026-07-15\"},\"cheapest_usd\":{\"price\":295}}"}]}' \
  /tmp/out.json
# read the result in CloudWatch (the MCP can't cat the invoke output):
aws logs filter-log-events --log-group-name /aws/lambda/flight-fare-notification \
  --query "events[].message" --region us-east-1
```
`RESEND_OK 200` → domain is effectively **Verified**, sandbox lifted. `403 validation_error` → not-yet-verified (have the student click Verify, step 3) or a stale cache (step 5). *(This is the same send the checklist's B3 runs — clean up its `notification_history` dedup row afterward so a real alert isn't suppressed; see [[m3-domain-checklist]].)*

> Don't flip the `from` secret before the student has clicked **Verify Domain** (step 3) — a custom `from` on an unverified domain gets rejected / spam-foldered ([[resend-best-practice]] Rule 2). And remember the cache-bust (step 5) — it's the #1 "I updated the secret but it still 403s" trap.

### Step 4 — Verify

Setup is done. **Run [[m3-domain-checklist]]** (the student can say 「驗收 M3」/「上線檢查」) — it verifies the things **M3 actually changes**: the domain serves HTTPS with a valid cert, Resend is Verified and email sends from your domain **reaching a non-owner inbox** (proving the M2 sandbox limit is lifted), and the secrets are internally consistent. (It deliberately does **not** re-run the payment journey — that's an M2 concern, since M3 doesn't touch the ECPay path — or grep the front-end bundle.)

> **Taking real money? (optional, separate skill)** Switching ECPay from the shared stage merchant to your own **production MerchantID** is **[[ecpay-go-live]]** — apply for a real merchant (3–5 工作日 review), confirm 信用卡定期定額 is enabled, put the real creds in `flight/ecpay` with `env:"prod"`, smoke-test + refund. It's optional; the course works fine staying on the stage merchant.

## Things to watch out for (setup gotchas)

1. **Default to a subdomain — don't ask** — bind `fly.yourdomain.com` (site) + `mail.yourdomain.com` (email) **by default**, even on a fresh domain (Step 1). It avoids clobbering another project, the apex-`A` record, the two-accounts Resend collision, **and re-verifying a domain the student already set up** (the delay we're avoiding). Only use the bare apex if the student explicitly asks.
2. **Never overwrite multi-value records** — `_vercel` TXT and SPF/DKIM TXT can already hold other values; in Route 53 `UPSERT` the record set with **all** values (existing + new), or you'll break the other project's verification (Step 2/3).
3. **Don't flip the Resend `from` secret before the domain is Verified** — rejected / spam until SPF+DKIM are live (Step 3).
4. **Cache-bust after changing `flight/resend`** — the Lambda caches the secret in warm containers; force a cold start or you'll see a stale-`from` `403` even though the secret is right (Step 3.5). This is the #1 confusing trap.
5. **Multi-account** — if the Route 53 zone and the flight project are in **different AWS accounts**, switch the `[default]` profile between DNS steps (domain account) and secret/Lambda steps (project account). For a single-account student, ignore.
6. **CORS stays `*` in this course (intentional)** — M3 does **not** lock CORS to the domain; `AllowOrigins: ["*"]` already allows the new domain, so nothing breaks and there's no CORS step. (Per-origin locking is a later hardening choice, not part of M3.) Note the ECPay **callback** routes are hit server-to-server, so CORS never applies to them anyway.
7. **You do NOT need to domain-map the API Gateway** — ECPay callbacks happily stay on `*.execute-api…` (invisible to users). Branded callback URLs are optional ACM+mapping work, not a requirement.
8. **DNS propagation** — records can take a few minutes; if the first check fails, wait and retry (the checklist handles this).

## Expected duration

30–90 minutes (mostly DNS waiting + Resend domain verification).

## Next step

After setup, run **[[m3-domain-checklist]]**. When it's all green: 「M3 完成！產品掛在你自己的網域、用你自己的品牌寄信，正式開張了 🎉 想加一個亮點功能嗎？跟我說『啟動 M4』，我們自架一台真的 OpenClaw 在 EC2 上，當你服務的 24 小時 AI 機票客服（Telegram 上回答航線/收費、要訂閱退訂就導去網頁）。」Then optionally load `m4-chat-to-subscribe`.

## Reference

- Vercel custom domains: https://vercel.com/docs/projects/domains
- Resend domain verification: https://resend.com/docs/dashboard/domains/introduction
- [[m3-domain-prerequisites]] — getting the domain string from the student (it's not discoverable from AWS) + the Route 53 cross-check.
- [[m3-domain-checklist]] — **all verification lives here.**
- [[resend-best-practice]] — Rule 2 (don't flip the `from` secret until Verified), and why the demo sender couldn't reach real users.
- [[ecpay-go-live]] — the optional stage→production ECPay switch.
