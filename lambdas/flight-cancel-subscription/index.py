"""flight-cancel-subscription: POST /cancel {email, route}.

Stops an `active` 信用卡定期定額 order at ECPay (CreditCardPeriodAction, Action=Cancel) and
then marks the row `cancelled`, keeping `current_period_end`: the subscriber keeps alerts
and can still change the target until the paid period ends, after which the parser moves
the row to `expired`.
"""

import base64
import calendar
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# CreditCardPeriodAction per flight/ecpay `env` (the AIO host, not the ECPG one). M2 is stage only.
PERIOD_ACTION_URLS = {"stage": "https://payment-stage.ecpay.com.tw/Cashier/CreditCardPeriodAction"}
# ECPay replies after which the order no longer bills, so the local cancel goes ahead:
# 1 = cancelled now; 90100150 = 不存在的訂單編號 (a synthetic or never-paid stage order);
# 90100149 = the order is already stopped (e.g. a retry after a lost response).
# Anything else (e.g. 10200073 CheckMacValue Error) means it still bills: 502, no change.
ECPAY_STOPPED = {"1", "90100150", "90100149"}
TIME_FORMAT ="%Y-%m-%dT%H:%M:%SZ"  # fixed width: the parser compares these as strings

# Per-environment names (infra/flight-m1.yaml `Env`); the defaults are prod's.
SECRETS_PREFIX = os.environ.get("SECRETS_PREFIX", "flight/")
secrets = boto3.client("secretsmanager")
sqs = boto3.client("sqs")
table = boto3.resource("dynamodb").Table(os.environ.get("SUBSCRIPTIONS_TABLE", "subscriptions"))
_ecpay = None  # cached per warm container; changing the secret needs a cold start
_status_queue_url = None


class BadRequest(Exception):
    pass


def handler(event, context):
    try:
        email, route = parse_request(event)
    except BadRequest as err:
        return respond(400, {"message": str(err)})

    key = {"email": email, "route": route}
    row = table.get_item(Key=key).get("Item")
    if row is None:
        return respond(404, {"message": "No subscription for this route."})
    if row.get("subscription_status") == "cancelled":
        return respond(200, row)  # a double click: already done, no second ECPay call or email
    if row.get("subscription_status") != "active":
        return respond(409, {"message": "Only an active subscription can be cancelled."})

    try:
        reply = cancel_at_ecpay(row.get("merchant_trade_no", ""))
    except (urllib.error.URLError, TimeoutError) as err:  # HTTPError (any non-2xx) is a URLError
        log(error="ECPay CreditCardPeriodAction unreachable; nothing changed", email=email, route=route,
            merchant_trade_no=row.get("merchant_trade_no", ""), detail=str(err))
        return respond(502, {"message": "Could not reach ECPay to stop the payments. Please try again."})
    if reply.get("RtnCode") not in ECPAY_STOPPED:
        log(error="ECPay refused to cancel; nothing changed", email=email, route=route,
            merchant_trade_no=row.get("merchant_trade_no", ""), rtn_code=reply.get("RtnCode"), rtn_msg=reply.get("RtnMsg"))
        return respond(502, {"message": "ECPay did not stop the payments. Please try again."})

    try:
        cancelled = mark_cancelled(row, datetime.now(timezone.utc))
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # A concurrent /cancel (or callback) changed the row between the read and the write.
        current = table.get_item(Key=key).get("Item")
        if current is not None and current.get("subscription_status") == "cancelled":
            return respond(200, current)
        return respond(409, {"message": "Only an active subscription can be cancelled."})
    log(cancelled=row.get("merchant_trade_no", ""), email=email, route=route,
        current_period_end=cancelled.get("current_period_end"))
    # Only a successful conditional update gets this far, so there is one email per cancel.
    enqueue_cancel_email(cancelled)
    return respond(200, cancelled)


def mark_cancelled(row, now):
    """`active` -> `cancelled`, keeping the paid period. Returns the updated row; raises
    ConditionalCheckFailedException if the row stopped being active in the meantime.
    A row activated before period tracking existed gets `now + 1 month` as its period end."""
    fallback_end = add_one_month(now)
    return table.update_item(
        Key={"email": row["email"], "route": row["route"]},
        UpdateExpression=(
            "SET subscription_status = :cancelled, cancelled_at = :now, updated_at = :now,"
            " current_period_end = if_not_exists(current_period_end, :fallback_end),"
            " current_period_end_date = if_not_exists(current_period_end_date, :fallback_end_date)"
        ),
        ConditionExpression="subscription_status = :active",
        ExpressionAttributeValues={
            ":cancelled": "cancelled",
            ":active": "active",
            ":now": now.strftime(TIME_FORMAT),
            ":fallback_end": fallback_end.strftime(TIME_FORMAT),
            ":fallback_end_date": fallback_end.strftime("%Y-%m-%d"),
        },
        ReturnValues="ALL_NEW",
    )["Attributes"]


def add_one_month(start):
    """The same instant one calendar month later, clamped to month end (ECPay's 31st rule)."""
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


def enqueue_cancel_email(row):
    """One `cancel` message on flight-status-queue (contract: spec #16). A failure is logged,
    not raised: the row is already cancelled, and a retried /cancel would not re-send it."""
    message = {"event_type": "cancel", "email": row["email"], "route": row["route"]}
    # cancelled_at identifies this cancel: flight-status-notification skips a redelivery of it.
    for key in ("plan_name", "current_period_end_date", "cancelled_at"):
        if row.get(key):
            message[key] = row[key]
    try:
        sqs.send_message(QueueUrl=status_queue_url(), MessageBody=json.dumps(message, ensure_ascii=False))
    except Exception as err:
        log(error="cancel email not enqueued; the subscription IS cancelled", email=row["email"],
            route=row["route"], detail=str(err))


def status_queue_url():
    global _status_queue_url
    if _status_queue_url is None:
        name = os.environ.get("STATUS_QUEUE_NAME", "flight-status-queue")
        _status_queue_url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    return _status_queue_url


def cancel_at_ecpay(trade_no):
    """POST a signed Action=Cancel for `trade_no`; returns ECPay's reply as a dict."""
    cfg = ecpay_config()
    params = {
        "MerchantID": cfg["merchant_id"],
        "MerchantTradeNo": trade_no,
        "Action": "Cancel",
        "TimeStamp": str(int(time.time())),
    }
    params["CheckMacValue"] = check_mac_value(params, cfg["hash_key"], cfg["hash_iv"])
    req = urllib.request.Request(
        cfg["period_action_url"],
        data=urllib.parse.urlencode(params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        reply = dict(urllib.parse.parse_qsl(resp.read().decode("utf-8"), keep_blank_values=True))
    log(ecpay_period_action="Cancel", merchant_trade_no=trade_no, reply=reply)
    return reply


def ecpay_config():
    """{merchant_id, hash_key, hash_iv, period_action_url} from flight/ecpay (stripped)."""
    global _ecpay
    if _ecpay is None:
        raw = json.loads(secrets.get_secret_value(SecretId=f"{SECRETS_PREFIX}ecpay")["SecretString"])
        cfg = {k: str(raw[k]).strip() for k in ("merchant_id", "hash_key", "hash_iv")}
        cfg["period_action_url"] = PERIOD_ACTION_URLS[str(raw.get("env") or "stage").strip()]
        _ecpay = cfg
    return _ecpay


def parse_request(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except ValueError:
        raise BadRequest("Request body must be JSON.")
    if not isinstance(body, dict):
        raise BadRequest("Request body must be a JSON object.")
    email, route = body.get("email"), body.get("route")
    if not isinstance(email, str) or not email.strip():
        raise BadRequest("email is required.")
    if not isinstance(route, str) or not route.strip():
        raise BadRequest("route is required.")
    return email.strip(), route.strip()


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def respond(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, default=_json_default, ensure_ascii=False),
    }


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, default=str))


# ---- CheckMacValue: copied on purpose into every ECPay Lambda. Keep the copies identical;
# tests/test_checkmacvalue.py runs each one against ECPay's known-answer vectors.
_CMV_UNESCAPE = {"%2d": "-", "%5f": "_", "%2e": ".", "%21": "!", "%2a": "*", "%28": "(", "%29": ")"}


def check_mac_value(params, hash_key, hash_iv):
    """ECPay AIO CheckMacValue (SHA256) over every key except CheckMacValue, blank values included."""
    pairs = sorted(((k, v) for k, v in params.items() if k != "CheckMacValue"), key=lambda kv: kv[0].lower())
    raw = f"HashKey={hash_key}&" + "&".join(f"{k}={v}" for k, v in pairs) + f"&HashIV={hash_iv}"
    encoded = urllib.parse.quote_plus(raw).replace("~", "%7E").lower()
    for escaped, char in _CMV_UNESCAPE.items():
        encoded = encoded.replace(escaped, char)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()
