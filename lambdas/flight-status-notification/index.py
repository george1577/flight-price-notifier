"""flight-status-notification: SQS consumer of `flight-status-queue`, one status change -> one email.

Message contract (spec #16): {event_type: "welcome" | "cancel", email, route, plan_name?,
current_period_end_date?, merchant_trade_no? (welcome), cancelled_at? (cancel)}. The
producers send at most one message per state change, but SQS may deliver it twice: the
trade number / cancelled_at identifies the change, and the subscription row records which
one has been emailed (see "redelivery" below). `event_type` picks the template; an unknown
one is logged and dropped.

Resend usage is the same as flight-fare-notification (constants, User-Agent, send_email
are copied on purpose: no shared module):
- 429 / 5xx / network errors are transient: raise, so SQS redelivers the message.
- 403 / 422 are permanent (e.g. the demo sender to a non-account address): log, drop.

The welcome email's monthly price is `amount` from flight/ecpay, the same value the
checkout charges.
"""

import html
import json
import urllib.error
import urllib.request

import boto3

RESEND_URL = "https://api.resend.com/emails"
# Resend sits behind Cloudflare: the default Python-urllib User-Agent gets a 403 (1010).
USER_AGENT = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
DEFAULT_FROM = "onboarding@resend.dev"  # demo sender until M3 verifies a domain
DROP_STATUSES = {403, 422}

# Deliberately duplicated from flight-fare-notification (tests/test_route_tables.py).
CITY_NAMES = {"TPE": "台北", "TYO": "東京", "SEL": "首爾", "LON": "倫敦"}

secrets = boto3.client("secretsmanager")
table = boto3.resource("dynamodb").Table("subscriptions")
_resend = None  # cached per warm container; changing the secret needs a cold start
_amount = None


def handler(event, context):
    # The event-source mapping uses BatchSize 1, so raising redelivers only this message.
    results = [notify(json.loads(record["body"])) for record in event.get("Records", [])]
    return {"ok": True, "results": results}


def notify(msg):
    event_type, email, route = msg.get("event_type"), msg.get("email"), msg.get("route")
    template = TEMPLATES.get(event_type)
    if template is None:
        log(event_type=event_type, to=email, route=route, note="unknown event_type - dropped")
        return "dropped"
    record_attr, sent_id = SENT_MARKERS[event_type], msg.get(IDENTITY_FIELDS[event_type])
    if sent_id and already_sent(email, route, record_attr, sent_id):
        log(event_type=event_type, to=email, route=route, sent_id=sent_id, note="already sent - SQS redelivery skipped")
        return "duplicate"
    subject, html_body, text_body = template(msg)
    status = send_email(to=email, subject=subject, html=html_body, text=text_body)
    if status is None:
        return "dropped"
    log(event_type=event_type, to=email, route=route, http_status=status, note="sent")
    if sent_id:
        record_sent(email, route, record_attr, sent_id)
    return "sent"


# ---------------------------------------------------------------- redelivery
# SQS delivers at least once, so the same message can arrive twice. Each message names the
# state change it announces (IDENTITY_FIELDS); once its email is sent, that value is
# recorded on the subscription row (SENT_MARKERS) and a redelivery of it is skipped. A
# message without the field (enqueued before it existed) is always sent.
IDENTITY_FIELDS = {"welcome": "merchant_trade_no", "cancel": "cancelled_at"}
SENT_MARKERS = {"welcome": "welcome_sent_for", "cancel": "cancel_email_sent_for"}


def already_sent(email, route, record_attr, sent_id):
    row = table.get_item(Key={"email": email, "route": route}).get("Item") or {}
    return row.get(record_attr) == sent_id


def record_sent(email, route, record_attr, sent_id):
    """Mark this email sent. A failure is logged, not raised: the email is already out, and
    raising would make SQS redeliver it."""
    try:
        table.update_item(
            Key={"email": email, "route": route},
            UpdateExpression="SET #sent = :sent_id",
            ConditionExpression="attribute_exists(email)",  # never create a row for a deleted subscription
            ExpressionAttributeNames={"#sent": record_attr},
            ExpressionAttributeValues={":sent_id": sent_id},
        )
    except Exception as err:
        log(to=email, route=route, sent_id=sent_id, detail=str(err),
            note="email sent but not recorded; a redelivery would send it again")


# ---------------------------------------------------------------- templates
def route_label(route):
    origin, destination = route.split("-", 1)
    return f"{CITY_NAMES.get(origin, origin)} → {CITY_NAMES.get(destination, destination)}"


def format_twd(amount):
    return f"NT${int(round(float(amount))):,}"


def render_html(heading, lines):
    esc = html.escape
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1f2937;line-height:1.5">',
        f'<h1 style="margin:0 0 12px;font-size:22px">{esc(heading)}</h1>',
        *(f'<p style="margin:0 0 8px">{esc(line)}</p>' for line in lines),
        "</div>",
    ]
    return "\n".join(parts)


def render_text(heading, lines):
    return "\n".join([heading, "", *lines])


# -- welcome
def welcome(msg):
    label = route_label(msg["route"])
    price = format_twd(monthly_amount())
    heading = f"歡迎訂閱 {label} 機票降價通知"
    lines = [
        f"您的 {label} 降價通知訂閱已開通，費用為每月 {price}。",
        "當票價低於您設定的目標價時，我們會寄信通知您。",
        "您可以隨時在網站的「我的訂閱」頁面取消訂閱，取消後服務會持續到已付費期間結束。",
    ]
    return f"✈️ {heading}", render_html(heading, lines), render_text(heading, lines)


# -- cancel (enqueued by flight-cancel-subscription, #21)
def cancel(msg):
    label = route_label(msg["route"])
    until = msg.get("current_period_end_date")
    heading = f"已取消 {label} 機票降價通知訂閱"
    lines = [f"您的 {label} 降價通知訂閱已取消，之後不會再扣款。"]
    if until:
        lines.append(f"已付費期間內仍會繼續收到降價通知：有效至 {until}。")
    lines.append("想再次追蹤這條航線時，可以在網站的「我的訂閱」頁面重新訂閱。")
    return f"✈️ {heading}", render_html(heading, lines), render_text(heading, lines)


TEMPLATES = {"welcome": welcome, "cancel": cancel}


# ---------------------------------------------------------------- config
def monthly_amount():
    """`amount` (TWD per month) from flight/ecpay, cached per warm container."""
    global _amount
    if _amount is None:
        raw = json.loads(secrets.get_secret_value(SecretId="flight/ecpay")["SecretString"])
        _amount = str(raw["amount"]).strip()
    return _amount


def resend_config():
    global _resend
    if _resend is None:
        raw = json.loads(secrets.get_secret_value(SecretId="flight/resend")["SecretString"])
        _resend = {"api_key": raw["api_key"], "from": raw.get("from") or DEFAULT_FROM}
    return _resend


def send_email(to, subject, html, text):
    """POST one email. Returns the 2xx status, None if dropped (403/422); raises if transient."""
    cfg = resend_config()
    body = {"from": cfg["from"], "to": [to], "subject": subject, "html": html, "text": text}
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return getattr(resp, "status", 200)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:500]
        if err.code in DROP_STATUSES:
            log(to=to, http_status=err.code, body=detail, note="permanent Resend error - dropped")
            return None
        log(to=to, http_status=err.code, body=detail, note="Resend error - raising for SQS redelivery")
        raise


def log(**fields):
    print(json.dumps({"fn": "flight-status-notification", **fields}, ensure_ascii=False, default=str))
