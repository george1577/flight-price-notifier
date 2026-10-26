# Flight Price Notifier: the architecture, explained from zero

This document explains every box and arrow in the "Flight Fare / Notification" architecture
figure: what each piece is, what it does in this project, and why it was designed that way.
It covers what was built in two closed specs:

- **#5, M1 Flight Price Checker**: subscribe to a route, check fares on a schedule, and email
  when the fare reaches your target. It's free and has no payment.
- **#16, M2 ECPay subscription**: NT$300 a month per route, paid through ECPay (綠界), so
  only paying users get alerts.

It's written for someone new to system design. Technical terms are kept where they matter,
and each one is explained the first time it appears. A glossary is at the end.

> **Where the figure and the code differ.** The figure is a simplified sketch. Section 9 lists
> where the real code differs, for example that there is no shared "Library Layer" and that
> the custom `{domain}.com` is not set up yet (that is M3). When the two disagree, trust the
> code and `infra/README.md`.

---

## 1. The big picture in one paragraph

A user signs in on the website, picks a route (for example Taipei → Tokyo), types the most
they're willing to pay, and pays NT$300 a month through ECPay. Every 30 minutes, a robot on
AWS asks a flight-price service, "what's the cheapest fare to Tokyo next month?" If that fare
is at or below someone's target, and that person has paid, the robot drops a note in a queue.
Another robot reads the queue and emails the person, without sending the same email over and
over. Payment events (paid, renewed, cancelled) come from ECPay and update the user's
subscription status. They also send welcome and cancellation emails through a second queue.

The figure has three big zones, and they match that story:

| Zone in the figure | What it's about | Main question it answers |
|---|---|---|
| **Top-left** (Cowork, GitHub, Product Site, Supabase, ECPay) | Building the product, the website, and payment | "How does a user sign up and pay?" |
| **Flight Fare Checker** (light blue, left) | Fetching prices and finding matches | "Who should be told about a cheap fare?" |
| **Notification** (light blue, right) + **Email** (yellow) | Sending emails | "How do we tell them, once and reliably?" |

Two grey **SQS** columns in the middle connect the zones.

---

## 2. Core ideas you need first

These five ideas explain most of the design decisions below.

### 2.1 Serverless and AWS Lambda (the λ boxes)

A **Lambda** is a small piece of code (here, one Python file, `index.py`) that AWS runs only
when something triggers it: an HTTP request, a timer, or a queue message. You don't rent or
manage a server that runs all day.

- **Why**: this app is idle most of the time. It works for a few seconds every 30 minutes,
  plus whenever a user clicks something. With Lambda you pay per run, which for this app
  rounds to almost nothing. There is no server to patch, restart, or scale.
- **Trade-off**: each run is short-lived and stateless. It can't remember anything between
  runs, except a small cache while the same "warm" copy is reused. Anything that must be
  remembered goes into a database (DynamoDB).

Every λ box in the figure is a Lambda. In this repo each one is a folder under `lambdas/`.

### 2.2 Decoupling with queues (the SQS columns)

**SQS** (Simple Queue Service) is a mailbox. One program puts messages in, and another takes
them out later.

- **Why**: if the price checker emailed people directly, a slow or failing email provider
  would slow down or crash the price checker. With a queue in between, the checker just drops
  "please notify X" notes and finishes. The emailer works through them at its own pace.
- **Retries for free**: if the emailer fails on a message (for example the email provider is
  briefly down), it doesn't delete the message. SQS makes it visible again after a while (the
  **visibility timeout**), and it's retried automatically.
- This pattern is called **producer / consumer**. The parser is a producer and the notifier
  is a consumer.

### 2.3 Source of truth

For each fact, exactly one component is allowed to decide it.

- *Is this user paid?* → Only **ECPay's server-to-server callbacks** decide that. The browser
  never can, because anyone can fake a browser request.
- *Who logged in?* → Only **Supabase Auth** decides that.
- *Which routes do we watch?* → The **routes file in S3**.

### 2.4 Idempotency

An operation is **idempotent** if doing it twice has the same effect as doing it once.
Networks are unreliable, so ECPay may send the same "payment succeeded" callback 4 times, and
SQS may deliver a message twice. Every handler here is written so a repeat changes nothing
and sends no second email.

### 2.5 Least privilege and secrets

- All Lambdas share one IAM role (`flight-lambda-role`). **IAM** is AWS's permission system.
  The role is allowed only the exact actions it needs, on the exact resources it needs. For
  example, it can read only one file in the S3 bucket.
- API keys (Travelpayouts, Resend, ECPay) live in **AWS Secrets Manager**, never in code or
  in the browser. The browser holds no AWS credentials at all.

---

## 3. Zone 1: build, website, sign-in, payment (top-left)

### 3.1 Cowork / Claude Code (orange) → Repo / GitHub (black)

**What**: the development workflow. You (with Claude Code / Cowork as the AI coding
assistant) write code, and it's stored in the GitHub repository
`george1577/flight-price-notifier`.

**Why GitHub is the hub**: GitHub is the single place everything deploys *from*:

- **Lovable** (the tool that generated the first landing page) syncs with the repo. That's why
  `AGENTS.md` forbids rewriting pushed history: it would break Lovable's copy.
- **GitHub Actions** (`.github/workflows/deploy.yml`) runs tests and deploys the backend to
  AWS whenever you push:
  - push to `dev` → the **dev** stack (`flight-dev`, all names suffixed `-dev`)
  - push to `main` → the **prod** stack (`flight-m1`), then it triggers Vercel to deploy the
    production website
- GitHub logs into AWS with **OIDC** (a short-lived token GitHub proves it owns), so no
  long-lived AWS password is stored in GitHub.

**Why two environments (dev and prod)**: you can test on `dev` without touching real users'
data or emails. Same template, different names, different secrets (`flight-dev/*`).

**Infrastructure as Code**: every AWS resource (tables, queues, Lambdas, API routes,
permissions) is described in one file, `infra/flight-m1.yaml`, a **CloudFormation**
template. Deploying means "make AWS match this file". Why: it's repeatable, reviewable in
git, and you can rebuild the whole system from scratch.

### 3.2 Product Site (purple, "Vercel host")

**What**: the website users see. It's a React single-page app (**SPA**): one HTML page whose
JavaScript draws all the screens. It's built with Vite, styled with shadcn components, and
hosted on **Vercel**. The key screen is the `/app` dashboard, which shows one **plan card**
per route (`src/components/PlanCard.tsx`).

**Why Vercel**: it serves static files from a global CDN, gives free HTTPS, and redeploys
from git automatically. A static SPA needs no server of its own.

**What the plan card shows**, driven by the subscription's status:

| Status in the database | What the card shows |
|---|---|
| no row | 「開始追蹤」 button |
| `pending_payment` (or an old M1 row with no status) | 未完成付款 + 「完成付款」 |
| `active` | 已訂閱 (有效), target price, 更新目標價, 「取消訂閱」 |
| `cancelled`, still inside the paid period | 已取消 · 有效至 <date>, target can still be updated |
| `expired`, or `cancelled` after the period ended | 已結束 + 「重新訂閱」 |

**Important detail the figure skips**: the website never talks to DynamoDB directly. It
calls an **HTTP API** (AWS API Gateway, named `flight-api`), and Lambdas behind that API read
and write the database (see section 3.5). The arrow from Product Site down to Subscriptions
really means "website → API → Lambda → table".

### 3.3 Database / Supabase (green)

**What**: Supabase is used for **authentication only**: sign-up, sign-in, and email
confirmation. The figure's "Database" label is a little misleading. No app data is stored in
Supabase: no tables and no subscriptions.

**Why split it like this**:

- Supabase gives you a complete, secure login system in minutes.
- All app data lives in one place (DynamoDB on AWS), next to the code that uses it.
- The two worlds are joined by **the user's email address**. Supabase says "this is
  alice@example.com", and DynamoDB rows are keyed by that email.

**Known gap (accepted for now)**: the API trusts the email the browser sends. It doesn't yet
verify the Supabase login token (**JWT**) on the server. Someone who knows your email could
start a checkout or cancel for you, but they could never *activate* a subscription without
paying. Fixing this is a planned follow-up.

### 3.4 `{domain}.com` (red vertical label)

**What**: your own domain name, for example `flightalert.com`, pointing at the website and
used as the email sender address.

**Status: not done yet. This is milestone M3.** Today the site lives at
`flight-price-notifier-bice.vercel.app`, and emails come from Resend's demo sender
`onboarding@resend.dev`. The demo sender can only deliver to the Resend account owner's own
inbox, so real users can't receive emails until M3 binds a verified domain.

**Why a domain matters for email**: inbox providers trust mail from a domain that proves,
through DNS records called **SPF** and **DKIM**, that the sender is allowed to send for it.
Without that, mail goes to spam or is refused.

### 3.5 The HTTP API (not drawn, but central)

API Gateway turns web requests into Lambda runs. These are the routes:

| Route | Lambda | Who calls it | What it does |
|---|---|---|---|
| `GET /subscriptions?email=` | `flight-list-subscriptions` | website | Returns the user's rows |
| `POST /subscribe` | `flight-save-subscription` | old website version | Saves the target, always JSON (kept for compatibility) |
| `POST /checkout` | `flight-save-subscription` | website | Paid row: update the target. Unpaid: start an ECPay checkout |
| `POST /cancel` | `flight-cancel-subscription` | website | Cancel the recurring payment |
| `POST /ecpay-return` | `flight-ecpay-return` | **ECPay's server** | First payment succeeded → activate |
| `POST /ecpay-period` | `flight-ecpay-period` | **ECPay's server** | A monthly renewal happened |
| `ANY /ecpay-result` | `flight-ecpay-result` | the user's **browser**, sent by ECPay | Redirects back to the dashboard |
| `GET /version` | `flight-version` | deploy script, dev banner | "Which backend code is running?" |

**CORS** (Cross-Origin Resource Sharing) is turned on so the browser, on the Vercel domain,
is allowed to call the API on the AWS domain.

**Why both `/subscribe` and `/checkout` exist**: the backend and the website deploy at
different moments. If `/subscribe` suddenly started returning an HTML payment page instead
of JSON, a user still running the old website would break. So `/subscribe` keeps its old
behaviour, and the new behaviour got a new route. This is the **expand → switch → contract**
rule in `infra/README.md`: never change an existing endpoint's response shape.

### 3.6 ECPay (blue, top) and the ECPay Lambda Handlers (blue λ)

**What ECPay is**: a Taiwanese payment company. We use its **信用卡定期定額** (credit-card
recurring payment) product. The user enters their card on ECPay's own page, called the
**cashier**, and ECPay charges it every month automatically. Today it runs on ECPay's public
**stage** (test) merchant, so no real money moves.

**Why use ECPay's hosted cashier instead of collecting card numbers ourselves**: handling
raw card numbers requires PCI-DSS compliance, which is expensive and risky. With a hosted
cashier, our servers never see a card number.

The figure's legend lists three handlers. Here is what each does:

- **`flight-ecpay-return`**, the `ReturnURL`. ECPay's server calls it once, after the
  **first** charge succeeds. It's the *only* thing that can turn a row `active`.
  - It verifies the **CheckMacValue** (explained below) and the MerchantID.
  - It sets `subscription_status=active` and `current_period_end` (paid-through date), using
    a **conditional update**: "only if not already active with this same order number".
  - Only if that update actually changed something does it put a `welcome` message on the
    status queue. A duplicate callback finds the condition false, so nothing changes and no
    second email is sent. That's idempotency.
  - It replies with exactly `1|OK`. That is ECPay's required acknowledgement; anything else
    and ECPay keeps retrying.
  - `SimulatePaid=1` (the stage back office's 「模擬付款」 button) is acknowledged but never
    activates anything, so it can't be used to hand out free access.
- **`flight-ecpay-period`**, the `PeriodReturnURL`. ECPay calls it for each **renewal**
  (month 2, 3, ...).
  - Success: move `current_period_end` forward one month and reset the failure counter.
  - Failure: count it, but keep the user active while ECPay retries. Only on the 6th
    consecutive failure, when ECPay itself gives up, does the row become `expired`. This way a
    paying customer isn't cut off by one temporary card decline.
- **`flight-cancel-subscription`**, `POST /cancel` from the user's 「取消訂閱」 button.
  - It tells ECPay to stop charging (`CreditCardPeriodAction`, `Action=Cancel`).
  - Then it marks the row `cancelled` and **keeps** `current_period_end`. The user already
    paid for this month, so alerts continue until it ends. This is a **grace period**.
  - It enqueues one `cancel` email. Cancelling twice is harmless.

Two more Lambdas take part in payment, though the figure doesn't list them:

- **`flight-save-subscription`** (`POST /checkout`) *starts* a payment. It writes the row as
  `pending_payment`, gives this attempt a unique order number (`merchant_trade_no`), and
  returns a small HTML page that auto-submits a signed form to ECPay's cashier. The browser
  shows that page and lands on ECPay.
- **`flight-ecpay-result`** (`OrderResultURL`). After paying, ECPay sends the *user's
  browser* back with a POST. A static SPA can't accept a POST (Vercel returns 405), so this
  tiny Lambda replies with a **302 redirect** to `/app?purchase=success` (or `failed`). It
  changes nothing in the database.

**CheckMacValue (CMV), explained**: it's a signature. ECPay and we share a secret
`HashKey` and `HashIV`. The sender sorts all the form fields, wraps them with the secret,
URL-encodes them in ECPay's specific way, and SHA-256-hashes the result. The receiver redoes
the same calculation, and if the two values differ, the message was forged or altered.

- **Why**: `/ecpay-return` is a public URL. Without the CMV check, anyone could POST
  "RtnCode=1, I paid" and get free service.
- **Gotchas the code handles**: empty fields such as `CustomField3=` *must* be included in
  the hash. `RtnCode` is compared as the text `"1"`. Every URL is trimmed before signing,
  because a stray newline changes the hash.
- The CMV function is copied into every Lambda that needs it. A test
  (`lambdas/tests/test_checkmacvalue.py`) runs every copy against ECPay's official test
  vectors so the copies can't drift apart.

### 3.7 The inset diagram (bottom-right): subscribe → ECPay → callback → write

The yellow inset compresses the payment flow. Here it is step by step:

```
 User clicks 開始追蹤 on the Product Site
   │
   ▼
 POST /checkout ──▶ flight-save-subscription
   │                 ├─ W(rite): subscriptions row = pending_payment, new merchant_trade_no
   │                 └─ returns auto-submit HTML form (signed with CMV)
   ▼
 Browser lands on ECPay cashier, user pays with card
   │
   ├──(server-to-server)──▶ POST /ecpay-return ──▶ flight-ecpay-return
   │                          verify CMV → row = active → enqueue "welcome"
   │                          reply "1|OK"
   │
   └──(browser)──▶ POST /ecpay-result ──▶ 302 to /app?purchase=success
                                              │
                                              ▼
                      Dashboard shows 「付款成功」 and re-fetches the list
                      until the card turns 已訂閱 (有效)
```

**Why two separate return paths?** The browser path is untrusted (the user could fake it)
and might never happen (they close the tab). The server-to-server path is signed and is
retried by ECPay. So the browser path only does user experience (the redirect and the
notice), and the server path is the source of truth for payment. That's also why the
dashboard *polls*: activation arrives asynchronously, usually a few seconds after the
redirect.

### 3.8 The subscription life cycle (state machine)

A **state machine** is a fixed list of states plus the only allowed moves between them.
Writing it down prevents impossible situations, such as "cancelled but never paid".

```
pending_payment ──(ECPay ReturnURL, paid)──▶ active ⇄ (update target price in place)
      ▲                                        │ user clicks 取消訂閱 (/cancel)
      │ re-subscribe + pay                     ▼
   expired ◀──(parser: paid period over)── cancelled  (grace: still gets alerts)
      ▲
      └──(6th failed renewal / series ended)── active
```

Who is allowed to make each move:

- **`flight-save-subscription`** only writes `pending_payment` (never `active`).
- **`flight-ecpay-return`** writes `pending_payment → active`.
- **`flight-ecpay-period`** extends `active`, or moves it to `expired` / `cancelled`.
- **`flight-cancel-subscription`** writes `active → cancelled`.
- **`flight-parser`** writes `cancelled → expired` once the paid period is over (see 4.5).
  It's a lazy cleanup: no separate scheduled job is needed.

---

## 4. Zone 2: Flight Fare Checker (left light-blue box)

This zone produces "fare match" messages. It flows left to right:
EventBridge → Parser Wrapper → Parser (one per route) → SQS.

### 4.1 Event Bridge (grey)

**What**: **Amazon EventBridge** is AWS's alarm clock. A rule (`flight-price-check`) with
`rate(30 minutes)` triggers the Parser Wrapper every 30 minutes.

**Why**: no server has to stay awake just to wait. Changing the frequency is a one-line
template change (`ScheduleExpression`), with no code change. On the **dev** stack the schedule
is off, and you invoke the wrapper by hand, so testing doesn't burn API quota.

### 4.2 Flight Routes [S3] (orange, "manual input", with the admin icon)

**What**: a small JSON file, `flight-routes.json`, stored in an **S3 bucket**
(`flight-config-<account>`). S3 is AWS's file storage. The file lists the routes to watch:

```json
[{"plan":"tokyo","origin":"TPE","destination":"TYO"},
 {"plan":"seoul","origin":"TPE","destination":"SEL"},
 {"plan":"london","origin":"TPE","destination":"LON"}]
```

(The figure was drawn with two routes. London was added later.)

**Why a config file instead of hard-coding the routes**: the operator (the admin icon) can
change *which routes the scheduler checks* without redeploying code. Orange in the legend
means "manual input". The committed copy is `infra/flight-routes.json`, and `deploy.sh`
uploads it on every deploy.

**Caveat**: a brand-new route also has to be added in four other places: the website cards,
the allowed plans in save-subscription, and the city names in both email Lambdas. A test
(`test_route_tables.py`) fails if the five lists disagree. Section 9 explains why the lists
are duplicated.

### 4.3 Parser Wrapper (green λ, `flight-parser-wrapper`)

**What**: it reads `flight-routes.json` from S3 and starts one `flight-parser` run **per
route**, asynchronously ("fire and forget"). It passes each one `from` and `to` (the
figure's arrow labels "1. from, 2. to").

**Why a wrapper and a separate parser (the fan-out pattern)**:

- **Isolation**: if the Seoul fetch is slow or crashes, Tokyo is unaffected. Each route has
  its own run, its own timeout, and its own logs.
- **Parallelism**: all routes are checked at the same time, not one after another.
- **Reuse**: the parser can be called for a single route or a single subscriber (see 4.6).

### 4.4 Parser (stacked green λ, `flight-parser`) and the 3rd-party Parser API [travelpayouts]

The stacked icon means "many copies running in parallel", one per route.

**What one parser run does**:

1. Reads the Travelpayouts API token from Secrets Manager.
2. Works out "next month" (for example `2026-11`).
3. Asks **Travelpayouts** (a flight-price data provider, the grey "3rd-party Parser API" box)
   for the cheapest fare on this route next month, **in TWD**. This is the gate. If there's
   no TWD fare, or the provider says 429 (too many requests), it logs "skip" and stops
   calmly. It doesn't crash.
4. Asks again **in USD**, as a best effort. If USD fails, the alert still goes out without
   the 約 US$ line.
5. Reads the subscribers for this route from the Subscriptions table (the pink arrow
   carrying "1. subscriber, 2. target price").
6. For every subscriber who **has paid** (4.5) and whose `target_price >= cheapest TWD fare`
   (note the "or equal": hitting your target exactly counts), it puts one message on the
   fare queue.

**The message** (the arrow labels "from, to, subscriber, target price, flight link") is
**self-contained**. It carries everything the emailer needs:

```json
{ "email": "...", "route": "TPE-TYO", "plan_name": "tokyo", "target_price": 12000,
  "cheapest":     {"price": 9325, "currency": "TWD", "airline": "LJ", "depart_date": "...", "return_date": "..."},
  "cheapest_usd": {"price": 298,  "currency": "USD", ...} }
```

**Why self-contained**: the consumer doesn't need to call Travelpayouts or look anything up
again. It can be retried on its own, and the price in the email is exactly the price that
matched. (The booking "flight link" is built from these fields in the notifier.)

**Why a custom User-Agent header**: some providers sit behind Cloudflare, which blocks
requests that look like anonymous scripts. This was a real failure in this stack.

### 4.5 Subscriptions [DynamoDB] (pink) and the "subscription check" (blue tag)

**What DynamoDB is**: AWS's serverless **NoSQL** key-value database. You look rows up by
key and pay per request (on-demand mode), so it's nearly free at this scale and needs no
server.

**The `subscriptions` table**:

- **Partition key**: `email`. **Sort key**: `route` (for example `TPE-TYO`). Together they
  mean "one row per user per route". Saving again for the same user and route *overwrites*
  the row (an **upsert**). It never creates a duplicate.
- Main attributes:
  - `target_price` and `plan_name`
  - `subscription_status`: `pending_payment` / `active` / `cancelled` / `expired`
  - `merchant_trade_no`: the ECPay order number
  - `current_period_end`: the paid-through moment, always formatted
    `YYYY-MM-DDTHH:MM:SSZ` so it can be compared as plain text
  - bookkeeping fields such as `activated_at`, `failed_charge_count`, `cancelled_at`

Pink in the legend means **user data**: data that belongs to users and must never be lost.
For that reason the table has `DeletionPolicy: Retain`, and the tests pin prod names so a
rename can't accidentally replace the table with an empty one.

**The "subscription check" is the paywall.** Before enqueueing anyone, the parser asks: is
this row paying?

- `active` → **yes**
- `cancelled` and `current_period_end >= now` → **yes** (grace period)
- `cancelled` and the period is over → **no**, and the parser flips the row to `expired`
  (conditionally: only if it's still `cancelled`, in case the user just re-subscribed)
- `pending_payment`, `expired`, or no status → **no**

**Why put the paywall in the parser** (the producer) and not in the emailer: unpaid users
never even enter the queue. It's the earliest point to filter, which saves work and keeps
the notifier simple and unchanged from M1.

### 4.6 Not in the figure: an immediate check when a paid user changes their target

If an `active` (or in-grace) user changes their target, `flight-save-subscription` immediately
starts the parser for **just that subscriber**. Otherwise they could wait up to 30 minutes
to learn the current fare already meets their new target. This is best effort: if it fails,
the save still succeeds and the next scheduled check catches it.

---

## 5. Zone 3: Notification (right light-blue box) and Email (yellow)

### 5.1 The two SQS queues (grey columns)

| Queue | Producer(s) | Consumer | Carries |
|---|---|---|---|
| `flight-fare-queue` (lower) | `flight-parser` | `flight-fare-notification` | "fare matched for this subscriber" |
| `flight-status-queue` (upper) | `flight-ecpay-return`, `flight-cancel-subscription` | `flight-status-notification` | `welcome` / `cancel` events |

Configuration details and why they matter:

- **Event-source mapping, BatchSize 1**: AWS polls the queue and calls the Lambda with
  **one** message at a time. Why one: if a batch of 10 failed on message 7, the whole batch
  would be retried, and messages 1 to 6 would email people twice.
- **Visibility timeout 180s**, about 6× the Lambda's 30s timeout. While a message is being
  processed it's hidden from other consumers. If the timeout were shorter than the Lambda's
  run time, a second copy could start before the first finished, which would send a
  duplicate email.
- **Permissions gotcha**: the consumer needs `ReceiveMessage`, `DeleteMessage` **and**
  `GetQueueAttributes`. Missing any one of them makes polling silently never happen.
- The dashed double arrow between the fare queue and Flight Fare Notification in the figure
  shows exactly that: the consumer receives messages *and* deletes them when done.

### 5.2 Flight Fare Notification (green λ, `flight-fare-notification`)

For each fare-match message:

1. **Check history**: it looks up the latest row in **Notification History** for this
   subscriber and route (the pink double arrow: it reads *and* writes).
2. **Dedup rule**: decide whether to send.
   - **Send** if never alerted before, or the last alert was more than 24 hours ago.
   - **Send** if, within 24 hours, the price dropped a lot more: at least 20% lower, or at
     least NT$2,000 lower than the last alerted price.
   - **Send** if the user changed their target after the last alert. The parser passes
     `subscription_updated_at`, which "re-arms" the dedup.
   - Otherwise, **skip** and log "skipped (deduped)".
   - **Why**: the checker runs every 30 minutes. Without dedup, a user would get 48 identical
     emails a day. The thresholds (`NOTIFY_FLOOR_HOURS`, `REALERT_PCT`, `REALERT_ABS_TWD`)
     are settings, so they can be tuned without a code change.
3. **Render the email**:
   - Subject line: 「✈️ 台北 → 東京 降價通知！NT$9,325 已達標」
   - Body: an NT$ headline, an optional 「約 US$」 line, airline and dates, and a
     「立即訂購」 button that deep-links to an Aviasales search
   - Both HTML and plain text are sent, because plain text improves deliverability and
     some mail apps show only text.
4. **Send via Resend**, then write the history row **only after** Resend confirms success
   (an HTTP 2xx response). Why: if the history row were written first and the send then
   failed, dedup would wrongly block the retry.
5. **Error handling**:
   - **429 / 5xx** (temporary: rate-limited or server down) → raise an error, so SQS retries
     later.
   - **403 / 422** (permanent: for example the demo sender can't email this address) → log
     and drop. Retrying would loop forever.

### 5.3 Notification History [DynamoDB] (pink)

**What**: one row per email actually sent.

- **Partition key** `pk = "<email>#<route>"`, **sort key** `sent_at` (an ISO-8601 UTC
  string).
- Other attributes: price and currency.

**Why this key design**: "give me the newest alert for alice on Tokyo" becomes one fast query
(partition = `alice#TPE-TYO`, sorted by time, newest first, limit 1). It also serves as an
audit log of everything sent.

### 5.4 Subscription Status Notification (green λ, `flight-status-notification`)

**What**: one consumer for *all* payment-related emails. It picks the template from
`event_type`:

- `welcome`: 「✈️ 歡迎訂閱 台北 → 東京 機票降價通知」, with the route and NT$ monthly price
- `cancel`: the route and the 有效至 (valid-until) date
- an unknown type is logged and dropped

**Why via a queue instead of emailing straight from the ECPay handlers**: ECPay expects
`1|OK` quickly and retries callbacks that are slow. Writing the row, enqueueing, and replying
takes milliseconds. Emailing can be slow and fail, so that happens later, with retries, in a
separate place. It also means there's **one** email path for status changes, with the same
Resend rules (retry 429/5xx, drop 403/422) as fare alerts.

**Double protection against duplicate emails**:

- The handlers only enqueue when their conditional database update actually changed the row.
- SQS can still occasionally deliver a message twice, so after sending, the consumer records
  `welcome_sent_for` / `cancel_email_sent_for` on the subscription row and skips a matching
  redelivery.

### 5.5 Email [Resend] (yellow boxes)

**What**: **Resend** is an email-sending service. Our Lambdas send each email with one HTTPS
POST to `api.resend.com`, using the plain Python standard library and no SDK.

**Why a service instead of our own mail server**: deliverability (not landing in spam), IP
reputation, bounces, and DNS signing are hard to get right. Resend handles them.

**Why two yellow boxes**: one for status emails, one for fare alerts. It's the same Resend
account and the same sender. They're drawn separately to show the two independent paths.

**Current limitation**: the sender is `onboarding@resend.dev` until M3, so mail only reaches
the Resend account owner's inbox (403 for anyone else). M3's `{domain}.com` fixes this.

---

## 6. The legend, decoded

| Legend item | Meaning |
|---|---|
| **Orange: manual input** | Things a human edits by hand: the routes file (and the Cowork box, the human-plus-AI developer) |
| **Green-grey: main component** | The core Lambdas that do the work |
| **Pink: user data** | Tables holding users' data, which must be protected and never lost |
| **Shared Structure: Library Layer + Lambda** | The idea of sharing common code through a Lambda Layer. **This project deliberately does not do this** (see section 9) |
| **ECPay Lambda Handlers: flight-ecpay-return / -period / flight-cancel-subscription** | The three payment-state Lambdas (section 3.6) |

---

## 7. End-to-end walkthroughs

### 7.1 "Alice subscribes to Tokyo and gets her first alert"

1. Alice signs in. **Supabase** confirms she is `alice@example.com`.
2. On `/app` she types NT$12,000 on the Tokyo card and clicks 開始追蹤.
3. `POST /checkout` → **save-subscription** writes the row (`pending_payment`, new order
   number) and returns the signed auto-submit form.
4. Her browser lands on the **ECPay cashier**, and she pays NT$300.
5. **ECPay** → `POST /ecpay-return` → CMV verified → the row becomes `active`, paid through
   one month from now → a `welcome` message goes on **flight-status-queue** → reply `1|OK`.
6. Her browser → `/ecpay-result` → 302 → `/app?purchase=success`. The notice appears, and the
   dashboard re-fetches until the card shows 已訂閱 (有效).
7. **status-notification** emails her the welcome message through **Resend**.
8. Within 30 minutes **EventBridge** triggers the **wrapper**, which reads the routes from
   **S3** and starts one **parser** per route.
9. The Tokyo parser asks **Travelpayouts**, which answers NT$9,325. Alice is `active` and
   12,000 ≥ 9,325, so a message goes on **flight-fare-queue**.
10. **fare-notification** finds no history row, sends the email, and writes a history row.
11. Thirty minutes later the same fare matches again. History says she was alerted under 24
    hours ago and the price hasn't dropped enough, so this one is skipped (deduped).

### 7.2 "Alice cancels"

1. She clicks 取消訂閱 and confirms. `POST /cancel` → **cancel-subscription** tells ECPay to
   stop the recurring charge.
2. The row becomes `cancelled`, and `current_period_end` is kept. A `cancel` email is queued
   and sent.
3. Until that date the parser still treats her as paying, so alerts continue.
4. The first parser run after that date flips her row to `expired`. Her card shows
   已結束 + 重新訂閱.

---

## 8. Operational and engineering practices behind the figure

- **Testing**: Python Lambdas are tested with `pytest` and **moto** (a fake AWS that runs on
  your laptop). The website is tested with **Vitest** and Testing Library. Tests check
  behaviour at the edges (HTTP responses, rows written, queue messages, outgoing requests),
  not internal helpers. Run them with `cd lambdas && uv run pytest`, `npx vitest run`, and
  `npx tsc --noEmit`.
- **Deploy safety**: after deploying, `deploy.sh` calls `GET /version` and fails unless the
  running backend reports the version it just deployed. In local development, a banner warns
  you when your code and the dev backend don't match.
- **Ordered deploys**: on `main`, the backend deploys first, then the website. A new website
  can't go live ahead of the backend it needs.
- **Backward compatibility**: a new backend must still serve the old website. A new website
  must show an error, never fail silently, against an old backend.
- **Region lock**: the template refuses to deploy outside `us-east-1`. Everything lives in
  one region, with no VPC (Lambdas need direct internet access to reach Travelpayouts, Resend
  and ECPay) and no SNS.
- **Logs**: every Lambda writes structured logs to **CloudWatch Logs**. Examples are the
  cheapest fare found, the match count, "skipped (deduped)", and both MACs when a CMV check
  fails. Debugging starts from these logs.

---

## 9. Where the figure is simplified or differs from the code

| Figure shows | Reality in the code |
|---|---|
| **"Shared Structure: Library Layer"** | **No Lambda Layer is used.** Each Lambda is one self-contained `index.py` (standard library + boto3). Small helpers (logging, CMV signing, city names) are **copied on purpose**, and tests keep the copies identical. Why: each Lambda deploys and reads on its own, with no version coupling between a layer and its users. The cost is duplication, which the parity tests contain. |
| Product Site → Subscriptions directly | Website → **API Gateway** → `flight-save-subscription` / `flight-list-subscriptions` → DynamoDB. The browser has no AWS access. |
| Supabase labelled "Database" | Supabase is **authentication only**. All app data is in DynamoDB. |
| `{domain}.com` on the site and emails | **Not done yet (M3).** Today: `*.vercel.app` and `onboarding@resend.dev`. |
| Three ECPay handlers | Plus `flight-save-subscription` (starts checkout via `/checkout`) and `flight-ecpay-result` (browser redirect). |
| ECPay handlers → SQS | Only `ecpay-return` (welcome) and `cancel-subscription` (cancel) enqueue. `ecpay-period` only updates the row. |
| Two routes | Three: Tokyo, Seoul, London. |
| One environment | Two stacks: `flight-dev` (schedule off, stage ECPay, `localhost` return URL) and `flight-m1` (prod). |
| Not shown | Secrets Manager, the shared IAM role, CloudWatch Logs, `GET /version`, the immediate parser run on a paid target change, and GitHub Actions deploys. |

---

## 10. Glossary

- **API Gateway (HTTP API)**: AWS service that receives web requests and runs a Lambda for each.
- **Asynchronous invoke**: start another function and don't wait for its result.
- **Callback / webhook**: a request a third party (ECPay) sends to *your* URL to tell you
  something happened.
- **CheckMacValue (CMV)**: ECPay's SHA-256 signature, which proves a message is genuine.
- **CloudFormation**: AWS Infrastructure-as-Code. A template file describes all resources.
- **Conditional update**: "change this row only if X is still true". It prevents races and
  duplicates.
- **CORS**: browser rules on which websites may call which APIs.
- **Dedup (deduplication)**: suppressing repeats of the same notification.
- **DynamoDB**: AWS's serverless NoSQL key-value database.
- **Partition key / sort key**: how DynamoDB finds a row. The partition key groups rows, and
  the sort key orders them within a group.
- **Event-source mapping**: AWS's built-in poller that feeds queue messages into a Lambda.
- **EventBridge**: AWS scheduler and event bus. It triggers things on a timetable.
- **Fan-out**: one job starts many parallel jobs, one per item.
- **Grace period**: time after cancelling during which you keep what you paid for.
- **IAM role / least privilege**: an AWS identity with only the permissions it needs.
- **Idempotent**: safe to do twice, with the same result as doing it once.
- **JWT**: a signed login token. Verifying it on the server proves who is calling.
- **Lambda**: a function AWS runs on demand. You pay per run, and there's no server to manage.
- **OIDC**: lets GitHub prove its identity to AWS without stored passwords.
- **PCI-DSS**: card-industry security rules. Avoided here by using ECPay's hosted cashier.
- **Producer / consumer**: one side writes messages to a queue, the other side processes them.
- **S3**: AWS file storage, made of "buckets" of "objects".
- **Secrets Manager**: AWS's encrypted store for API keys.
- **Server-to-server (S2S)**: a request between two backends. The user's browser is not involved.
- **SPA (single-page app)**: a website whose screens are all drawn by JavaScript on one page.
- **SQS**: AWS message queue.
- **Visibility timeout**: how long SQS hides a message while it's being processed.
- **Stage (ECPay)**: ECPay's test environment, which uses test cards and moves no real money.
- **State machine**: a fixed set of states and allowed transitions.
- **Upsert**: insert if missing, overwrite if present.
