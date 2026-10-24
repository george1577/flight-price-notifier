"""flight-ecpay-return: POST /ecpay-return (ECPay's ReturnURL, server to server).

ECPay calls this once the first charge of a 信用卡定期定額 order has gone through. A real,
paid callback flips the (CustomField1, CustomField2) = (email, route) row from
`pending_payment` to `active` and starts its first paid period. This Lambda (and, for
renewals, flight-ecpay-period) is the only writer of `active`. Each activation enqueues
exactly one `welcome` message on flight-status-queue (STATUS_QUEUE_NAME) for
flight-status-notification; resends and SimulatePaid enqueue nothing.

ECPay retries until it gets exactly `1|OK`, so every processed callback, no-ops included,
gets that reply.
"""

import base64
import calendar
import hashlib
import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # fixed width: the parser compares these as strings

secrets = boto3.client("secretsmanager")
sqs = boto3.client("sqs")
table = boto3.resource("dynamodb").Table("subscriptions")
_ecpay = None  # cached per warm container; changing the secret needs a cold start
_status_queue_url = None


def handler(event, context):
    fields = read_form(event)
    cfg = ecpay_config()
    received = fields.get("CheckMacValue", "")
    expected = check_mac_value(fields, cfg["hash_key"], cfg["hash_iv"])
    if received.upper() != expected:
        log(rejected="CheckMacValueInvalid", received_mac=received, computed_mac=expected, fields=fields)
        return reply(400, "0|CheckMacValueInvalid")
    if fields.get("MerchantID") != cfg["merchant_id"]:
        log(rejected="MerchantIDMismatch", merchant_id=fields.get("MerchantID"), fields=fields)
        return reply(400, "0|MerchantIDMismatch")

    log(callback="ReturnURL", fields=fields)  # the full verified payload, for the operator
    trade_no = fields.get("MerchantTradeNo", "")
    if fields.get("SimulatePaid") == "1":
        log(simulated_payment=trade_no, note="stage 模擬付款; nothing written")
        return reply(200, "1|OK")
    if fields.get("RtnCode") != "1":  # a string: "1" is the only success code
        log(charge_failed=trade_no, rtn_code=fields.get("RtnCode"), rtn_msg=fields.get("RtnMsg"),
            note="row stays pending_payment")
        return reply(200, "1|OK")

    activated = activate(fields.get("CustomField1", ""), fields.get("CustomField2", ""), trade_no, fields)
    if activated is not None:
        log(activated=trade_no, email=activated["email"], route=activated["route"],
            current_period_end=activated["current_period_end"])
        # Only a successful conditional update gets this far, so a resend can never
        # enqueue a second welcome.
        enqueue_welcome(activated)
    return reply(200, "1|OK")


def enqueue_welcome(row):
    """One `welcome` message on flight-status-queue (contract: spec #16). A failure is logged,
    not raised: the row is already active, so an ECPay retry could not re-send it anyway."""
    message = {"event_type": "welcome", "email": row["email"], "route": row["route"]}
    # merchant_trade_no identifies this activation: flight-status-notification skips a redelivery of it.
    for key in ("plan_name", "merchant_trade_no"):
        if row.get(key):
            message[key] = row[key]
    try:
        sqs.send_message(QueueUrl=status_queue_url(), MessageBody=json.dumps(message, ensure_ascii=False))
    except Exception as err:
        log(error="welcome email not enqueued; the subscription IS active", email=row["email"],
            route=row["route"], detail=str(err))


def status_queue_url():
    global _status_queue_url
    if _status_queue_url is None:
        name = os.environ.get("STATUS_QUEUE_NAME", "flight-status-queue")
        _status_queue_url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    return _status_queue_url


def activate(email, route, trade_no, fields):
    """Flip the (email, route) row to `active` for this paid trade number and start its first
    period. Returns the updated row, or None when there was nothing to activate (no such row,
    a resend, or a row that is already active or has moved on)."""
    key = {"email": email, "route": route}
    row = table.get_item(Key=key).get("Item") if email and route else None
    if row is None:
        log(warning="paid callback matches no subscription", email=email, route=route, merchant_trade_no=trade_no)
        return None

    now = datetime.now(timezone.utc)
    period_end = add_period(now, row.get("period_type") or "M", int(row.get("frequency") or 1))
    try:
        return table.update_item(
            Key=key,
            UpdateExpression=(
                "SET subscription_status = :active, merchant_trade_no = :trade_no, activated_at = :now,"
                " last_charged_at = :now, total_success_times = :times, failed_charge_count = :zero,"
                " current_period_end = :period_end, current_period_end_date = :period_end_date"
            ),
            # Only an unpaid row: never re-stamp an active row (a resend) or revive a
            # cancelled/expired one (a late resend). Legacy rows have no status.
            ConditionExpression=(
                "attribute_exists(email)"
                " AND (subscription_status = :pending OR attribute_not_exists(subscription_status))"
            ),
            ExpressionAttributeValues={
                ":active": "active",
                ":pending": "pending_payment",
                ":trade_no": trade_no,
                ":now": now.strftime(TIME_FORMAT),
                ":times": int(fields.get("TotalSuccessTimes") or 1),
                ":zero": 0,  # a re-subscriber must not inherit an earlier series' declines
                ":period_end": period_end.strftime(TIME_FORMAT),
                ":period_end_date": period_end.strftime("%Y-%m-%d"),
            },
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        explain_no_op(table.get_item(Key=key).get("Item") or row, trade_no)
        return None


def explain_no_op(row, trade_no):
    status, current = row.get("subscription_status"), row.get("merchant_trade_no")
    if status == "active" and current == trade_no:
        log(resend=trade_no, note="already active for this trade number; nothing changed")
    elif status == "active":
        log(warning="a second checkout was paid while the subscription is already active; "
                    "check for a double charge (refunds are manual)",
            email=row["email"], route=row["route"], active_trade_no=current, paid_trade_no=trade_no)
    else:
        log(ignored=trade_no, status=status, merchant_trade_no=current,
            note="paid callback for a row that is no longer awaiting payment")


def add_period(start, period_type, frequency):
    """One billing period after `start`: D = days; M/Y = calendar months, clamped to month end."""
    if period_type == "D":
        return start + timedelta(days=frequency)
    months = frequency * 12 if period_type == "Y" else frequency
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def ecpay_config():
    """{merchant_id, hash_key, hash_iv} from flight/ecpay (stripped), cached per warm container."""
    global _ecpay
    if _ecpay is None:
        raw = json.loads(secrets.get_secret_value(SecretId="flight/ecpay")["SecretString"])
        _ecpay = {k: str(raw[k]).strip() for k in ("merchant_id", "hash_key", "hash_iv")}
    return _ecpay


def read_form(event):
    """The form-urlencoded body as a dict, blank values kept (they are part of the CMV)."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))


def reply(status, text):
    return {"statusCode": status, "headers": {"content-type": "text/plain; charset=utf-8"}, "body": text}


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
