---
name: m1-flight-price-checker-prerequisites
description: One-time setup before Milestone 1 of the Flight Price Notifier course — an INTERACTIVE, agent-driven walkthrough. MOST of the foundation was already done in M0 (AWS access wired, GitHub PAT cached in `flight/github`, Supabase url/publishable-key cached in `flight/supabase`) — so this skill VERIFIES those from Secrets Manager and only ASKS the student if something is actually missing. The genuinely-new M1 keys are Travelpayouts (the parser reads it) and Resend (the notifier reads it); the agent collects, caches, and verifies those — Resend via a throwaway Lambda. Use when the student starts M1 for the first time, or when `m1-flight-price-checker` / `-checklist` detects the project, AWS access, the Travelpayouts token, or Resend is missing.
---

# M1 Prerequisites — interactive setup (the agent drives)

**You are the Cowork agent running this skill. Drive the student through it one part at a time** — don't dump the whole thing. For each part: tell them what's about to happen, **check Secrets Manager first**, ask for a value **only if it's actually missing**, then run the AWS/git work yourself, confirm it worked, and move on.

> **Most of the foundation already exists from M0.** In M0 the student wired **AWS access**, cached the **GitHub PAT**, and cached the **Supabase** url + publishable key. So **don't re-ask for those by default** — discover them in Secrets Manager and reuse them. The only **genuinely new** M1 keys are **Travelpayouts** and **Resend**. Net effect: for a student who finished M0 cleanly, the *only* things you actually collect here are those two keys.

> ⚠️ **Don't assume the secret *name*.** M0 is run by an AI in a separate session, and **the name it chose is not guaranteed** — it might be `flight/github`, `flight-price-notifier/github`, `github-pat`, etc. So **never** rely on a hard-coded `describe-secret --secret-id flight/github`: an exact-name miss does **not** mean the secret is absent — it usually means it's stored under a different name. **Discover by listing + scanning** (below), and only treat a credential as missing after the scan finds nothing.

## Architecture (what these credentials unlock)

![Flight Fare / Notification architecture (M1) — Cowork pushes to the GitHub repo, which deploys the Vercel Product Site (Supabase auth). The Flight Fare Checker group: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the 3rd-party travelpayouts API and scans Subscriptions [DynamoDB]; matches go to SQS. The Notification group: Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and sends via Email [Resend]. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight-notification-architecture-m1.png)

The four credentials you confirm/collect here are what make this diagram run:
- **GitHub PAT** (M0 carryover) → the `Cowork → Repo (GitHub) → Product Site (Vercel)` push loop on the left.
- **Supabase** url + publishable key (M0 carryover) → the `Database [Supabase]` auth behind the Product Site.
- **Travelpayouts** token (new) → the `3rd-party Parser API [travelpayouts]` the `Parser` Lambdas call.
- **Resend** key (new) → the `Email [Resend]` box the `Flight Fare Notification` Lambda sends through.

## How to run this (read first)

- **Discover-then-reuse, ask only if genuinely absent.** For each M0 carryover, run **`list-secrets`** and scan the names for an obvious candidate (e.g. anything containing `github` / `supabase`); confirm by reading it and checking the **shape** (a `pat` field for GitHub, a `url`+`publishable_key`/`anon_key` for Supabase). If found → "✅ already cached from M0 — reusing" and move on. **Only if the scan turns up nothing** do you ask the student to paste it. (This is [[aws-best-practice]] Rule 2's check-then-collect, but name-agnostic — biased toward *reuse* because M0 already did the work.)
- **One canonical scan, reused throughout.** List once at the top and keep the result; don't re-list per credential:
  ```bash
  aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"   # the full inventory to scan against
  ```
  Match case-insensitively on substrings, not exact names. If two candidates match (e.g. a stray duplicate), read both and prefer the one whose shape is right; mention the ambiguity to the student rather than guessing silently.
- **Tolerate the *shape*, not just the name.** A past M0 run may have stored a credential as **bare-string** rather than JSON (real example: `github/personal-access-token` holds the raw `github_pat_…`, not `{"pat":…}`), or used a different field name (`anon_key` vs `publishable_key`). So **don't hard-fail on a `json.load`** — if it doesn't parse as JSON, treat the raw value as the credential; and match key fields by their value pattern, not an exact field name. Identify the secret by *what the value looks like*, not its envelope.
- **One part at a time, conversationally.** End each part by confirming success and announcing the next part. Never ask for two different keys in the same message.
- **You run all the AWS/git commands** via the AWS API MCP + git tool. The student never runs `aws` themselves.
- **Every AWS command:** `--region us-east-1`, `[default]` profile (no `--profile`).
- **Read [[aws-best-practice]] *Cowork execution constraints* once** before you deploy anything. Two facts shape this skill: (1) the common AWS connector is **`aws`-only** (no shell/`zip`/file authoring) → Lambda code deploys via **inline CFN** (small) or the **`flight-seed` S3 bridge** (big); (2) the **sandbox can't reach arbitrary hosts** (`api.resend.com` is proxy-blocked) → anything that POSTs to a third party runs **from a Lambda**, not the sandbox. This is why Resend is verified from a Lambda (Part D).

**The four secrets M1 relies on — two are M0 carryovers, two are new here.** The "looks like" column is what you scan `list-secrets` for; the M0 ones may sit under *any* name the M0 run chose, so match by substring + shape, not exact name:

| Credential | Looks like (name substring / JSON shape) | Where it comes from | Note |
|---|---|---|---|
| GitHub PAT | name `*github*`; shape `{pat:"github_pat_…"}` or bare string | **M0 (Step 6)** — discover, don't re-ask | write-credential — real secret |
| Supabase url + publishable key | name `*supabase*`; shape `{url, publishable_key}` (or `{url, anon_key}`) | **M0 (Step 9)** — discover, don't re-ask | publishable key is **public** — convenience cache (never the service-role key) |
| Travelpayouts token | you create it (suggested `flight/travelpayouts`, shape `{token}`) | **NEW in M1** — collect here | real secret (the parser Lambda reads it) |
| Resend API key | you create it (suggested `flight/resend`, shape `{api_key, from}`) | **NEW in M1** — collect here | real secret (the notification Lambda reads it) |

> For the two **new** secrets you create here, use the suggested `flight/*` names for consistency with the rest of the course ([[aws-best-practice]] Rule 2) — but the M1 build skill should **also** look those up by substring/shape, for the same reason: a later session can't assume the name either. The point isn't the prefix; it's that **discovery, not a hard-coded name, is how every session finds an existing secret.**

**Opening line to the student (say something like):**
> "Good news — M0 already wired your AWS and cached your GitHub + Supabase keys, so I'll just confirm those are still in Secrets Manager. Then M1 only needs **two new keys**: Travelpayouts and Resend. I'll ask for those one at a time and store + test them. Let me check what's already there first."

---

## Part 0 — Project links (mostly recoverable — only the Vercel URL is new)

You need a few of the student's M0 links, but **don't blanket-ask for all three** — recover what you can first:
- **Supabase URL** → already in the Supabase secret you'll discover in Part B3 (read its `url`). No need to ask.
- **GitHub repo URL** → if you're connected to GitHub, list the student's repos and find `flight-price-notifier` (or whatever they named it); only ask if you can't identify it.
- **Vercel deploy URL** → **not** stored anywhere, so this is the one you genuinely need from them.

So the only thing to actually ask for up front:

> "One link, please — your **Vercel deploy URL** (e.g. `https://fly-low-alert.vercel.app/`). I'll pull your GitHub repo and Supabase project from what's already cached."

Echo it back to confirm. If you couldn't auto-identify the GitHub repo, ask for that too — otherwise leave it. (A wrong Vercel URL makes the optional Part B2 push-check fail confusingly, so double-check that one.)

---

## Part A — AWS access (already wired in M0 — just confirm)

**AWS was set up in M0 (Step 5)** so the GitHub token could be cached. So **don't walk the student through IAM again by default — just confirm it still works:**
```bash
aws sts get-caller-identity --query Account --output text --region us-east-1
```
- Returns an account ID → "✅ AWS is still wired from M0." Skip straight to Part B.
- Errors (`[default]` profile missing/expired) → *then* fall back to the one-time setup below.

<details><summary><strong>Fallback — first-time AWS setup (only if the check above failed)</strong></summary>

This is the only part the student does outside Cowork — because writing `~/.aws/credentials` needs a local Claude Code CLI session (the Cowork connector then reads those creds). **Walk them through it, then wait for them to confirm AWS is live before continuing.**

**Tell the student, step by step (pause between):**

1. **Install the AWS API MCP connector** in Cowork: *Customize → Connectors → search "AWS API MCP" → Install.*
2. **In the AWS Console, as the root user** of their course account:
   - **IAM → Users → Create user** (suggest `admin-for-cowork`) → attach **`AdministratorAccess`** → create.
   - That user → **Security credentials → Create access key → "Command Line Interface (CLI)"** → copy the **Access key ID + Secret** (shown once).
3. **Open a Claude Code CLI session** (not Cowork) and paste this prompt **there** — fill in the two values:

   > I have new AWS credentials to configure. Access key ID: `<YOUR_ACCESS_KEY_ID>`. Secret access key: `<YOUR_SECRET_ACCESS_KEY>`. Detect Mac/Linux vs Windows for the right path (`~/.aws/credentials` or `%USERPROFILE%\.aws\credentials`), write the **`[default]`** profile with these values (preserving any other existing profiles), then test with `aws sts get-caller-identity`.

4. **Come back to Cowork and tell me when `aws sts get-caller-identity` worked.**

**Then YOU verify it from Cowork** before moving on:
```bash
aws sts get-caller-identity --query Account --output text --region us-east-1
```
- Returns an account ID → say "✅ AWS is wired — now I can cache your keys. Next: GitHub." and go to Part B.
- Errors → the `[default]` profile isn't written yet; have them redo step 3 in the CLI.

> ⚠️ Remind them: **root is used only once** (to make the admin user); never use root keys after. Revoke the access key at course end.

</details>

---

## Part B — GitHub + Supabase (M0 carryovers — discover, don't re-ask)

Both were **cached in M0**, but **under whatever name that run chose** — so **discover them by scanning `list-secrets`, never by a hard-coded `--secret-id`**. Run the canonical list once (from "How to run this") and scan it; only ask the student if the scan finds nothing.

**B1 — Discover the GitHub secret** (scan, don't assume the name *or* the shape):
1. From the `list-secrets` inventory, pick the name that looks like the GitHub token — anything containing **`github`** (real examples seen in the wild: `flight/github`, `github/personal-access-token`, `github-pat`).
2. Read it and pull out the token — **accept either shape**, because M0 runs vary:
   ```bash
   aws secretsmanager get-secret-value --secret-id "<the name you found>" --region us-east-1 --query SecretString --output text
   ```
   - It may be **JSON** (`{"pat":"github_pat_…"}`) → use the `pat` field.
   - It may be a **bare token string** (`github_pat_…` with no JSON wrapper — this is common; e.g. `github/personal-access-token` stores it raw) → use the whole string as the token. Don't `json.load`-and-fail; if parsing as JSON throws, treat the raw value as the PAT.
   - Either way, sanity-check it **starts with `github_pat_`** (fine-grained) or `ghp_` (classic).
   - Found a usable token → "✅ GitHub token already cached from M0 (`<name>`) — reusing it." **Remember that exact name** for B2 and for the build skill. Move to B3.
   - **No `github`-ish secret anywhere in the list** (rare — M0 skipped/partial) → **then** ask for the PAT:
     > "I don't see a GitHub token cached yet. Paste me your **GitHub fine-grained PAT** — github.com/settings/personal-access-tokens → *Generate new token (fine-grained)* → *Only select repositories* → this one repo → *Repository permissions → Contents → Read and write* → copy the `github_pat_…`."

     Wait (⚠️ write-credential — don't echo it back), then cache it under the course-standard name:
     ```bash
     aws secretsmanager create-secret --name flight/github --secret-string '{"pat":"<their token>"}' --region us-east-1
     ```

**B2 — (Optional) confirm the push loop still works.** M0 already proved push→Vercel (the SPA conversion push + the live deploy), so this is a light re-check, not a fresh setup. If you want certainty before building, recall the token **from the secret you found in B1** (by its discovered name — never re-paste) and do a quick title round-trip:
```bash
aws secretsmanager get-secret-value --secret-id "<the github secret name from B1>" --region us-east-1   # parse the "pat"
```
Use that `pat` to clone (into a **native dir**, not the FUSE-mounted workspace — git locking fails there with `config.lock: Operation not permitted`; the working copy is ephemeral, GitHub + Vercel are the source of truth), bump the title to **"Flight Price Notifier V3"**, push to their repo, confirm the Vercel deploy shows it, then revert. If it fails: the PAT lacks Contents:RW, or GitHub→Vercel auto-deploy is off — fix before building. Tell the student "✅ push loop confirmed — I always pull your token from the secret, never ask again."

**B3 — Discover the Supabase secret** (scan, don't assume the name *or* the exact field keys):
1. From the same inventory, pick the name containing **`supabase`**.
2. Read it — expect a `url` plus a publishable/anon key, but **tolerate field-name variants**:
   ```bash
   aws secretsmanager get-secret-value --secret-id "<the name you found>" --region us-east-1 --query SecretString --output text
   #   → e.g. {"url":"https://….supabase.co","publishable_key":"sb_publishable_…"}
   #     the key field may instead be "anon_key", "publishableKey", "key" — match on the sb_publishable_*/eyJ… value, not the field name
   ```
   - Found a `url` + a browser-safe key (in whatever field) → "✅ Supabase url + publishable key already cached from M0 (`<name>`) — reusing." (Read the `url` here to satisfy Part 0.) Move to Part C.
   - **No `supabase`-ish secret in the list** → **then** ask:
     > "I don't see your Supabase values cached yet. Open your Supabase project → **Project Settings → API**, and paste me two values: the **Project URL** (`https://….supabase.co`) and the **publishable key** (`sb_publishable_*` — the browser-safe key; **NOT** the service-role key)."

     Wait, then cache under the course-standard name:
     ```bash
     aws secretsmanager create-secret --name flight/supabase --secret-string '{"url":"<their url>","publishable_key":"<their key>"}' --region us-east-1
     ```
     > The publishable key is **public by design** (it ships in the browser bundle) — caching it is pure convenience; no Lambda reads it. **If they paste a `service_role` / secret key, stop them** — never cache that (see [[supabase-best-practice]] Rule 2).

---

## Part C — Travelpayouts (ask, verify, cache)

> **These last two (Travelpayouts, Resend) are secrets *you* create in this session** — so you control the name and shape, and an exact-name `describe-secret` before creating is correct (no discovery scan needed; you know the name because you're about to set it). The discover-by-scan rule only applies to the **M0 carryovers** above, which a *different* session named. Still — store them at the suggested `flight/*` with the documented JSON shape so the later build skill can find them the same way.

**Ask the student for their Travelpayouts token.** Say:
> "Paste me your **Travelpayouts API token**. Get it free at travelpayouts.com → connect the **Aviasales** program → Profile → API token. (Just the token — no `marker` needed; that's an optional booking-commission add-on for later.)"

Wait, then **verify it works** by fetching this URL (a GET — the agent web-fetch can do this; it's not proxy-blocked like Resend's POST):
```
https://api.travelpayouts.com/v1/prices/cheap?origin=TPE&destination=TYO&depart_date=2026-07&currency=usd&token=<their token>
```
Confirm the JSON has `"success":true` (a `401`/`"success":false` = wrong token or Aviasales not connected — have them fix it). It works for both `twd` and `usd` — one token, no new secret.

Then **cache it** (check-then-collect):
```bash
aws secretsmanager describe-secret --secret-id flight/travelpayouts --region us-east-1 --query "Name"
aws secretsmanager create-secret --name flight/travelpayouts --secret-string '{"token":"<their token>"}' --region us-east-1
```
Tell the student "✅ Travelpayouts verified + cached."

---

## Part D — Resend (ask, cache, verify from a Lambda)

**Ask the student for their Resend key + account email.** Say:
> "Two values, please: your **Resend API key** (`re_…` — sign up free at resend.com → API Keys → Create API Key; a **Sending-access** key is enough), and the **email you signed up to Resend with**. ⚠️ On the demo sender (`onboarding@resend.dev`), Resend only delivers to that **account email** — a send anywhere else looks fine in the log but never arrives. So this email is also what we'll use as your test subscriber later."

Wait, then **cache it** (check-then-collect) — `from` is fixed, `test_to` is their account email:
```bash
aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query "Name"
aws secretsmanager create-secret --name flight/resend --secret-string '{"api_key":"<their key>","from":"onboarding@resend.dev","test_to":"<their account email>"}' --region us-east-1
```

**Then verify by sending from a throwaway Lambda** — the Cowork sandbox can't POST to `api.resend.com` (proxy-blocked), but a Lambda has internet. Deploy `flight-resend-test` via **inline CFN** (handler `index.handler`, python3.12, Role `arn:aws:iam::<ACCOUNT_ID>:role/flight-lambda-role`, Timeout 15), JSON-escaping this code into `Code.ZipFile`:
```python
import json, urllib.request, boto3
def handler(e, c):
    s = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId="flight/resend")["SecretString"])
    body = {"from": s["from"], "to": s["test_to"], "subject": "resend test", "html": "<p>it works</p>", "text": "it works"}
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + s["api_key"], "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (compatible; flight-notifier/1.0)"}, method="POST")  # UA: Resend is behind Cloudflare
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("RESEND_OK", r.status, r.read().decode())
    except urllib.error.HTTPError as ex:
        print("RESEND_ERR", ex.code, ex.read().decode())
    return {"done": True}
```
Poll `aws cloudformation describe-stacks --stack-name flight-resend-test --query "Stacks[0].StackStatus" --region us-east-1` until CREATE_COMPLETE (the connector can't use `cloudformation wait`). Then invoke + read the logs:
```bash
aws lambda invoke --function-name flight-resend-test --payload '{}' out.json --region us-east-1
aws logs filter-log-events --log-group-name /aws/lambda/flight-resend-test --query "events[].message" --region us-east-1
```

> ⚠️ **This needs `flight-lambda-role`**, which `m1-flight-price-checker` Step 3 creates. If the student is doing the prereq strictly before any building, **create that role now** (see the build skill Step 3) or run this Resend verify right after build-Step 3.

**Read the result + tell the student:** `RESEND_OK 200 {"id":"..."}` in the logs **and** the email in their Resend-account inbox (check spam) = ✅. Errors: `401` = wrong key; `403`/`422` = a `from` they can't send from (stay on `onboarding@resend.dev` until M3); `403` body `error code: 1010` = Cloudflare blocked the UA (the handler sets one — only bites if it was dropped). When it passes, tear down: `aws cloudformation delete-stack --stack-name flight-resend-test --region us-east-1`.

---

## Wrap-up — confirm all green, then hand off

Run a final check and report to the student:
```bash
aws sts get-caller-identity --query Account --output text --region us-east-1                 # AWS wired
aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"                # scan the inventory; confirm a github-ish, supabase-ish, travelpayouts, and resend secret are all present (don't assume exact names)
```
Confirm to the student, in plain language:
- ✅ **AWS** wired (`[default]` profile) — carried over from M0.
- ✅ **All four credentials present in Secrets Manager:** the GitHub + Supabase secrets (**discovered + reused from M0**, whatever names they sit under) and the new Travelpayouts + Resend ones (**cached this run** as `flight/travelpayouts` / `flight/resend`) — *you'll never have to paste any of these again; I look them up in Secrets Manager every session.*
- ✅ **Travelpayouts** `"success":true`; ✅ **Resend** test email arrived.

Then say: **"Setup's done. Say『啟動 M1』and I'll build the whole notifier — subscribe, scheduled fetch, and the alert email."** Load `m1-flight-price-checker`.
