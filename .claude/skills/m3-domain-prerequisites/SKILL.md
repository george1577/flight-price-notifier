---
name: m3-domain-prerequisites
description: Prerequisites before M3 of the Flight Price Notifier course — a domain (or subdomain) you control, whose DNS may live in Route 53 (manageable via the AWS MCP) OR an external registrar; plus confirmation the M2 paid product works on the Vercel URL before binding. Covers the multi-account (domain vs project in different AWS accounts) and shared-domain (bind a subdomain) cases. Use when the student starts M3, or when `m3-domain` / `-checklist` detects the domain or M2 carryover is missing.
---

# M3 Prerequisites — Domain + M2 carryover

## What this skill does

M3 adds one thing: a **domain (or subdomain) you control**. Its DNS may live in **Route 53** (manage it directly via the AWS MCP — the smoothest path) or an **external registrar** (Namecheap/Cloudflare/GoDaddy). Everything else carries over from M2 (the live-on-Vercel paid product). This skill identifies the domain + where its DNS lives, and confirms the M2 carryover.

## Architecture

![Flight Fare / Notification architecture (M3) — the full M2 system the domain (these prerequisites identify) gets bound onto in two places. The Product Site is relabeled [domain].com (Vercel host) instead of *.vercel.app — that relabel IS the M3 change. The Product Site POSTs to the ECPay Lambda Handlers (flight-ecpay-return / flight-ecpay-period / flight-cancel-subscription), which write subscription_status onto Subscriptions [DynamoDB]; a "subscription check" gate on that table makes the Parser scan only active rows. On payment events the handlers enqueue to the Notification-side SQS, where the Subscription Status Notification Lambda emails welcome/cancel via Resend — bound to alerts@[domain].com in M3. The notifier flow: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the travelpayouts API, scans Subscriptions, enqueues matches to the Flight Fare Notification SQS → Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and emails via Resend. The two places M3 binds the domain — the Product Site box and the Resend email sender — are the only edges that change. Inset: the subscribe → ECPay → callback (W = write) loop. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight_notification_structure2.jpg)

## When to load this skill

- "M3 環境準備" / any time M3 detects the domain or the M2 product is missing.

## Step 1 — Auto-detect the domain, its DNS home, and the host to bind (don't ask)

M3 uses **one** domain (or subdomain) in two places: the **Vercel site** AND the **Resend email sender** (`alerts@<host>`). Discover everything you can yourself first — **the goal is to proceed without prompting**, because every confirmation round delays the run.

**Auto-list the Route 53 zones** (via the AWS MCP) — the domain is usually an AWS-hosted zone you can manage end-to-end:
```bash
aws route53 list-hosted-zones --query "HostedZones[].{name:Name,id:Id,private:Config.PrivateZone}"
# a public zone whose Name == "<domain>." (trailing dot) is the one you'll edit
```

Then act on what you find — **no decision table to walk the student through, just pick and state it:**

- **Exactly one owned public zone** → that's the domain; DNS is in Route 53, so you add every Vercel/Resend record yourself via `aws route53 change-resource-record-sets` (`UPSERT`). Note the `HostedZoneId` and move on.
- **No public zone matches** → DNS is external (registrar / Cloudflare / Vercel DNS). This is the *one* genuinely un-discoverable case — get the domain string from the student and hand them the records to add. (Private/internal-VPC zones don't count — ignore them, treat as "no public zone.")
- **Multiple owned public zones, truly ambiguous** → the only case worth a question. Even then, **don't block: pick the most plausible one, state your choice ("Using `<domain>` — say so if you meant another"), and continue.** A wrong guess is cheap to correct; a blocking prompt stalls the whole run.

**Auto-detect whether the apex is in use** (drives the host choice below — also self-serve, don't ask):
```bash
# any existing apex A / www CNAME / other project records in the zone = apex is in use
aws route53 list-resource-record-sets --hosted-zone-id <id> \
  --query "ResourceRecordSets[?Name=='<domain>.' || Name=='www.<domain>.'].{name:Name,type:Type}"
```
Existing apex `A` / `www` `CNAME` (or any live project records) → **apex is in use** → bind a subdomain (the default anyway, below).

> **Don't assume the domain is un-lookable.** (An earlier version claimed "AWS has no record of it, you must ask." That's only true when DNS is *external* — a Route 53 zone is fully discoverable and editable through the MCP. List first; only the external-DNS case needs the student.)

### ⚠️ Multi-account: the Route 53 zone may be in a DIFFERENT AWS account than the flight resources

The AWS MCP uses a single `[default]` profile. In a real setup the **domain (Route 53)** and the **flight project (Lambdas/secrets/API Gateway)** can live in **different AWS accounts** — so you'll **switch the `[default]` profile** between *DNS steps* (domain account) and *secret/Lambda steps* (project account). Identify both up front:
- Which account/profile holds the **Route 53 zone** → used for all DNS record changes.
- Which account/profile holds **`flight/*` secrets + the Lambdas + `flight-api`** → used for the `put-secret-value`, cold-start, etc.
- For a **student** this is usually **one** account — flag the two-account case as the advanced variant. (See [[aws-best-practice]].)

### Bind a SUBDOMAIN by default — don't ask, never clobber the apex

**Default to a dedicated subdomain** for the flight project — `fly.yourdomain.com` for the site and `mail.yourdomain.com` for the Resend sender — **even when the apex is free, and without asking the student.** It avoids clobbering another project on the same domain, sidesteps the apex-`A` record and the "same apex verified in two Resend accounts" collision, and — the big one — **doesn't force the student to re-verify a domain they may already have set up, which would delay the build** (see [[m3-domain]] Step 1/3). Every DNS write is an **`UPSERT`/append**, never a blind overwrite — multi-value records (like `_vercel` TXT) must be appended to, not replaced (see [[m3-domain]] Step 2). Only use the bare apex if the student explicitly asks for it. Decide the exact host(s) here so the later steps use them consistently.

**Verify:** you know (a) the exact host(s) you'll bind (a subdomain by default), and (b) where their DNS lives (Route 53 account/profile, or an external registrar the student can edit).

## Step 2 — Confirm M2 carryover (the product works on the Vercel URL)

```bash
# the live Vercel site responds
curl -sS -o /dev/null -w "%{http_code}\n" https://<your>.vercel.app          # 200
# ECPay callback Lambdas exist (M2)
for fn in flight-ecpay-return flight-ecpay-period; do aws lambda get-function --function-name $fn --region us-east-1 --query 'Configuration.FunctionName'; done
# resend + ecpay secrets exist (list all names + scan — backtick JMESPath filters error through the AWS API MCP)
aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"   # flight/travelpayouts, /resend, /ecpay, + the cache /github, /supabase
```
If the paid flow isn't working on the Vercel URL yet, finish M2 first — M3 only changes the *address*, not the logic.

## Step 3 — Access to everything you'll touch

Confirm access to: **DNS** (the Route 53 account/profile **or** the external registrar), **Vercel** (adding a domain to the project is a **manual dashboard step** — there's no Vercel-domain MCP tool in Cowork), **Resend** (domain verification), and — only if switching ECPay to prod — the **ECPay 廠商後台** (`vendor.ecpay.com.tw`).

## Verify (all must pass)

- **Host is auto-selected** (subdomain — `fly.<domain>` — by default, and always when the apex is in use); **do not prompt for confirmation — it delays the run** ✅
- The exact host(s) to bind are decided (a subdomain by default; bare apex only if the student explicitly asked) ✅
- You know where the DNS lives and can edit it — Route 53 (via MCP) **or** an external registrar ✅
- If domain and project are in **different AWS accounts**, you've identified which profile to use for DNS vs. for secrets/Lambdas ✅
- Vercel URL returns 200 with the paid product ✅
- `flight-ecpay-return` + `flight-ecpay-period` + all `flight/*` secrets exist (in the project account) ✅

## Next step

Return to `m3-domain` Step 1.
