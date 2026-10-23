"""flight-fare-notification: SQS consumer of `flight-fare-queue`, one match -> one email.

Per message (contract: spec #5, produced by flight-parser):
1. should_send(): the dedup / re-alert check (ticket #10) against the latest
   notification_history row; NOTIFY_FLOOR_HOURS / REALERT_PCT / REALERT_ABS_TWD env vars.
2. Render the email inline (subject / render_html / render_text / booking_url).
3. POST to Resend with the `flight/resend` credentials and a custom User-Agent.
4. Only after a 2xx, write a `notification_history` row.

Failure classes (M1 has no DLQ, so a permanent failure must not loop):
- 429 / 5xx / network errors are transient: raise, so SQS redelivers the message.
- 403 / 422 are permanent (e.g. the demo sender to a non-account address): log, drop.
"""

import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

RESEND_URL = "https://api.resend.com/emails"
# Resend sits behind Cloudflare: the default Python-urllib User-Agent gets a 403 (1010).
USER_AGENT = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
DEFAULT_FROM = "onboarding@resend.dev"  # demo sender until M3 verifies a domain
DROP_STATUSES = {403, 422}

secrets = boto3.client("secretsmanager")
history_table = boto3.resource("dynamodb").Table("notification_history")
_resend = None  # cached per warm container; changing the secret needs a cold start
_UNSET = object()
_marker = _UNSET


def handler(event, context):
    # The event-source mapping uses BatchSize 1, so raising redelivers only this message.
    results = [notify(json.loads(record["body"])) for record in event.get("Records", [])]
    return {"ok": True, "results": results}


def notify(msg):
    email, route = msg["email"], msg["route"]
    origin, destination = route.split("-", 1)
    # The TWD quote is the gate and the headline; USD is a separate, optional quote.
    fare = {**msg["cheapest"], "origin": origin, "destination": destination}
    usd = msg.get("cheapest_usd") or {}
    usd_price = usd.get("price")
    target_price = msg.get("target_price")

    if not should_send(email, route, fare):
        log(to=email, route=route, price=fare["price"], note="skipped (deduped)")
        return "skipped"

    marker = affiliate_marker()
    status = send_email(
        to=email,
        subject=subject(fare),
        html=render_html(fare, target_price, marker=marker, usd_price=usd_price),
        text=render_text(fare, target_price, marker=marker, usd_price=usd_price),
    )
    if status is None:
        return "dropped"
    record_sent(email, route, fare)
    log(to=email, route=route, price=fare["price"], http_status=status, note="sent")
    return "sent"


def should_send(email, route, fare):
    """Dedup / re-alert gate, checked BEFORE calling Resend. TWD (`cheapest`) only.

    Send if there is no prior alert for pk="{email}#{route}", or the last one is older
    than NOTIFY_FLOOR_HOURS. Inside that window, send only on a big further drop:
    new <= last * (1 - REALERT_PCT/100)  or  last - new >= REALERT_ABS_TWD.
    """
    items = history_table.query(
        KeyConditionExpression=Key("pk").eq(f"{email}#{route}"),
        ScanIndexForward=False,  # newest sent_at first
        Limit=1,
    ).get("Items", [])
    if not items:
        return True
    last = items[0]
    floor_hours = float(os.environ.get("NOTIFY_FLOOR_HOURS", "24"))
    sent_at = datetime.fromisoformat(str(last["sent_at"]).replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - sent_at > timedelta(hours=floor_hours):
        return True
    pct = Decimal(os.environ.get("REALERT_PCT", "20"))
    abs_twd = Decimal(os.environ.get("REALERT_ABS_TWD", "2000"))
    last_price, new_price = Decimal(str(last["price"])), Decimal(str(fare["price"]))
    return new_price <= last_price * (1 - pct / 100) or (last_price - new_price) >= abs_twd


# ---------------------------------------------------------------- renderer
CITY_NAMES = {"TPE": "台北", "TYO": "東京", "SEL": "首爾"}
BOOKING_BASE = "https://www.aviasales.com/search/"


def city(code):
    return CITY_NAMES.get(code, code)


def format_twd(amount):
    return f"NT${int(round(float(amount))):,}"


def usd_text(amount):
    return f"約 US${int(round(float(amount))):,}"


def day(iso):
    """'2026-11-12T08:15:00+08:00' -> '2026-11-12' (the local departure date)."""
    return str(iso)[:10] if iso else None


def subject(fare):
    return f"✈️ {city(fare['origin'])} → {city(fare['destination'])} 降價通知！{format_twd(fare['price'])} 已達標"


def booking_url(fare, marker=None):
    """Aviasales deep link ORIGIN+DDMM+DEST+DDMM (return part only when there is a return date)."""

    def ddmm(iso):
        d = day(iso)
        return f"{d[8:10]}{d[5:7]}" if d else ""

    path = f"{fare['origin']}{ddmm(fare.get('depart_date'))}{fare['destination']}{ddmm(fare.get('return_date'))}"
    url = BOOKING_BASE + path
    if marker:
        url += "?" + urllib.parse.urlencode({"marker": marker})
    return url


def _details(fare, target_price):
    """(label, value) lines shared by the HTML and text bodies."""
    lines = [("目標價", format_twd(target_price))] if target_price is not None else []
    if fare.get("airline"):
        lines.append(("航空公司", fare["airline"]))
    if day(fare.get("depart_date")):
        lines.append(("去程", day(fare["depart_date"])))
    if day(fare.get("return_date")):
        lines.append(("回程", day(fare["return_date"])))
    return lines


def render_html(fare, target_price, marker=None, usd_price=None):
    esc = html.escape
    route_label = f"{city(fare['origin'])} → {city(fare['destination'])}"
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1f2937;line-height:1.5">',
        f'<p style="margin:0 0 4px;color:#6b7280">{esc(route_label)} 機票降價通知</p>',
        f'<h1 style="margin:0 0 8px;font-size:28px">{esc(format_twd(fare["price"]))}</h1>',
    ]
    if usd_price is not None:
        parts.append(f'<p style="margin:0 0 12px;color:#6b7280">{esc(usd_text(usd_price))}</p>')
    for label, value in _details(fare, target_price):
        parts.append(f'<p style="margin:0">{esc(label)}：{esc(str(value))}</p>')
    parts.append(
        f'<p style="margin:20px 0"><a href="{esc(booking_url(fare, marker), quote=True)}" '
        'style="display:inline-block;padding:10px 20px;background:#2563eb;color:#ffffff;'
        'text-decoration:none;border-radius:6px">立即訂購</a></p>'
    )
    parts.append('<p style="margin:0;font-size:12px;color:#9ca3af">票價隨時變動，以訂購頁面為準。</p>')
    parts.append("</div>")
    return "\n".join(parts)


def render_text(fare, target_price, marker=None, usd_price=None):
    lines = [f"{city(fare['origin'])} → {city(fare['destination'])} 機票降價通知", "", format_twd(fare["price"])]
    if usd_price is not None:
        lines.append(usd_text(usd_price))
    lines.append("")
    lines += [f"{label}：{value}" for label, value in _details(fare, target_price)]
    lines += ["", f"立即訂購：{booking_url(fare, marker)}", "", "票價隨時變動，以訂購頁面為準。"]
    return "\n".join(lines)


def resend_config():
    global _resend
    if _resend is None:
        raw = json.loads(secrets.get_secret_value(SecretId="flight/resend")["SecretString"])
        _resend = {"api_key": raw["api_key"], "from": raw.get("from") or DEFAULT_FROM}
    return _resend


def affiliate_marker():
    """Optional Travelpayouts `marker` from flight/travelpayouts. Best effort: never blocks a send."""
    global _marker
    if _marker is _UNSET:
        try:
            raw = json.loads(secrets.get_secret_value(SecretId="flight/travelpayouts")["SecretString"])
            value = raw.get("marker") if isinstance(raw, dict) else None
            marker = str(value).strip() if value else ""
            _marker = marker or None
        except Exception as err:  # missing secret, plain-string token, no permission...
            log(note="no affiliate marker", error=str(err))
            _marker = None
    return _marker


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


def record_sent(email, route, fare):
    history_table.put_item(
        Item={
            "pk": f"{email}#{route}",
            "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "email": email,
            "route": route,
            "price": Decimal(str(fare["price"])),
            "currency": fare["currency"],
        }
    )


def log(**fields):
    print(json.dumps({"fn": "flight-fare-notification", **fields}, ensure_ascii=False, default=str))
