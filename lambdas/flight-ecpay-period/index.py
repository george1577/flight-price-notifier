"""flight-ecpay-period: POST /ecpay-period (ECPay's PeriodReturnURL, server to server).

From the second charge of a 信用卡定期定額 order on, ECPay charges the card on its own
schedule and posts each result here, for the (CustomField1, CustomField2) = (email, route)
row whose merchant_trade_no is the order's MerchantTradeNo.

- A paid renewal moves current_period_end forward one period from its previous value.
- A failed renewal only counts (failed_charge_count). ECPay retries a declined card itself
  and ends the series on the 6th consecutive failure, so the row turns `expired` only then.
- When the last of ExecTimes charges is paid, the series is over: the row turns
  `cancelled`, and the parser expires it once the paid period ends.

ECPay retries until it gets exactly `1|OK`, so every processed callback, no-ops included,
gets that reply.
"""

import base64
import calendar
import hashlib
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # fixed width: the parser compares these as strings
MAX_FAILED_CHARGES = 6  # ECPay terminates the series on the 6th consecutive failed charge

secrets = boto3.client("secretsmanager")
table = boto3.resource("dynamodb").Table("subscriptions")
_ecpay = None  # cached per warm container; changing the secret needs a cold start


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

    # The renewal field set isn't confirmed against a live callback yet: log all of it.
    log(callback="PeriodReturnURL", fields={k: v for k, v in fields.items() if k != "CheckMacValue"})
    trade_no = fields.get("MerchantTradeNo", "")
    if fields.get("SimulatePaid") == "1":
        log(simulated_payment=trade_no, note="stage 模擬付款; nothing written")
        return reply(200, "1|OK")
    row = find_row(fields.get("CustomField1", ""), fields.get("CustomField2", ""), trade_no)
    if row is None:
        return reply(200, "1|OK")

    if fields.get("RtnCode") == "1":  # a string: "1" is the only success code
        renew(row, fields)
    else:
        count_failure(row, fields)
    return reply(200, "1|OK")


def find_row(email, route, trade_no):
    """The (email, route) row this order belongs to, or None (logged) if there isn't one."""
    row = table.get_item(Key={"email": email, "route": route}).get("Item") if email and route else None
    if row is None:
        log(warning="renewal callback matches no subscription", email=email, route=route, merchant_trade_no=trade_no)
    return row


def renew(row, fields):
    """A paid renewal: move the paid-through date forward one period from its previous value.
    Returns the updated row, or None (logged) when this charge doesn't apply to the row."""
    now = datetime.now(timezone.utc)
    trade_no = fields.get("MerchantTradeNo", "")
    times = int(fields.get("TotalSuccessTimes") or int(row.get("total_success_times") or 1) + 1)
    previous = row.get("current_period_end")
    start = datetime.strptime(previous, TIME_FORMAT).replace(tzinfo=timezone.utc) if previous else now
    period_end = add_period(start, row.get("period_type") or "M", int(row.get("frequency") or 1))
    # The last of ExecTimes charges: ECPay stops here, so the row lapses when this period ends.
    # Otherwise the status is left alone (active stays active, a cancelled row stays cancelled).
    exec_times = int(fields.get("ExecTimes") or row.get("exec_times") or 0)
    series_complete = 0 < exec_times <= times
    try:
        updated = table.update_item(
            Key={"email": row["email"], "route": row["route"]},
            UpdateExpression=(
                "SET last_charged_at = :now, total_success_times = :times, failed_charge_count = :zero,"
                " current_period_end = :period_end, current_period_end_date = :period_end_date"
                + (", subscription_status = :cancelled" if series_complete else "")
            ),
            # This order's charge, on a paid-up row, not seen before (a resend carries the same
            # TotalSuccessTimes), and from the date we read (so two deliveries can't both add).
            ConditionExpression=(
                "merchant_trade_no = :trade_no AND subscription_status IN (:active, :cancelled)"
                " AND (attribute_not_exists(total_success_times) OR total_success_times < :times)"
                " AND " + ("current_period_end = :previous" if previous else "attribute_not_exists(current_period_end)")
            ),
            ExpressionAttributeValues={
                ":now": now.strftime(TIME_FORMAT),
                ":times": times,
                ":zero": 0,
                ":period_end": period_end.strftime(TIME_FORMAT),
                ":period_end_date": period_end.strftime("%Y-%m-%d"),
                ":trade_no": trade_no,
                ":active": "active",
                ":cancelled": "cancelled",
                **({":previous": previous} if previous else {}),
            },
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        explain_no_op(table.get_item(Key={"email": row["email"], "route": row["route"]}).get("Item") or row, fields)
        return None
    log(renewed=trade_no, email=row["email"], route=row["route"], status=updated["subscription_status"],
        total_success_times=updated["total_success_times"], current_period_end=updated["current_period_end"])
    if series_complete:
        log(series_complete=trade_no, exec_times=exec_times,
            note="last scheduled charge; cancelled, and the parser expires it when the period ends")
    return updated


def count_failure(row, fields):
    """A declined renewal. ECPay retries the card itself, so the subscriber keeps service; the
    row expires only once ECPay has ended the series (the 6th consecutive failure, or a payload
    that says so). Returns the updated row, or None (logged) when the failure doesn't apply."""
    key = {"email": row["email"], "route": row["route"]}
    trade_no = fields.get("MerchantTradeNo", "")
    try:
        updated = table.update_item(
            Key=key,
            UpdateExpression="ADD failed_charge_count :one",
            ConditionExpression="merchant_trade_no = :trade_no AND subscription_status IN (:active, :cancelled)",
            ExpressionAttributeValues={":one": 1, ":trade_no": trade_no, ":active": "active", ":cancelled": "cancelled"},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        explain_no_op(table.get_item(Key=key).get("Item") or row, fields)
        return None

    failures = int(updated["failed_charge_count"])
    log(charge_failed=trade_no, email=row["email"], route=row["route"], failed_charge_count=failures,
        rtn_code=fields.get("RtnCode"), rtn_msg=fields.get("RtnMsg"))
    ended = series_ended(fields)
    if failures < MAX_FAILED_CHARGES and not ended:
        return updated
    try:
        updated = table.update_item(
            Key=key,
            UpdateExpression="SET subscription_status = :expired",
            ConditionExpression="merchant_trade_no = :trade_no AND subscription_status IN (:active, :cancelled)",
            ExpressionAttributeValues={
                ":expired": "expired", ":trade_no": trade_no, ":active": "active", ":cancelled": "cancelled",
            },
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return None  # it moved on in between (e.g. already expired)
    log(expired=trade_no, email=row["email"], route=row["route"], failed_charge_count=failures,
        reason="series ended" if ended else f"{failures} consecutive failed charges")
    return updated


def series_ended(fields):
    """Whether a renewal callback says ECPay has terminated the series. ExecStatus is the
    period-query API's status field (0 = terminated); PeriodReturnURL isn't documented to
    carry it, so this is unconfirmed until a real terminated payload has been logged."""
    return fields.get("ExecStatus") == "0"


def explain_no_op(row, fields):
    trade_no, status = fields.get("MerchantTradeNo", ""), row.get("subscription_status")
    if row.get("merchant_trade_no") != trade_no:
        log(warning="renewal for an order that is not the row's current one; check the ECPay back office "
                    "for a series that should have been cancelled",
            email=row["email"], route=row["route"], row_trade_no=row.get("merchant_trade_no"), paid_trade_no=trade_no)
    elif status not in ("active", "cancelled"):
        log(warning="renewal charged a subscription that is not paid up; check it in the ECPay back office",
            email=row["email"], route=row["route"], status=status, merchant_trade_no=trade_no)
    else:
        log(resend=trade_no, total_success_times=fields.get("TotalSuccessTimes"),
            note="this charge was already applied; nothing changed")


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
