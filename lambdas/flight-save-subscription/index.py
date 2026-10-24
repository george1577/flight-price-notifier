"""flight-save-subscription: POST /subscribe {email, plan_name, target_price}.

An `active` (paid) row just gets the new target, in place, and the row comes back as JSON.
Anything else upserts one `subscriptions` row per (email, route), marks it
`pending_payment` with a fresh MerchantTradeNo, and returns an auto-submit HTML form that
sends the browser to ECPay's cashier for a 信用卡定期定額 (monthly recurring) order. Only
the ECPay callbacks may set `active`; this Lambda never sets it and never downgrades it.

The billing period on the form and the row (PeriodType / Frequency / ExecTimes) comes from
the PERIOD_TYPE / FREQUENCY / EXEC_TIMES environment variables (default M / 1 / 999).
"""

import base64
import hashlib
import html
import json
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import boto3

PLANS = {
    "tokyo": {"origin": "TPE", "destination": "TYO"},
    "seoul": {"origin": "TPE", "destination": "SEL"},
    "london": {"origin": "TPE", "destination": "LON"},
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ECPay cashier per flight/ecpay `env`. M2 runs on stage only; production is M3.
CASHIER_URLS = {"stage": "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"}
TAIWAN = timezone(timedelta(hours=8))
# Plain text only: words like curl/python trip ECPay's WAF.
ITEM_NAME = "機票降價通知 月費訂閱"
TRADE_DESC = "Flight Price Notifier monthly plan"

secrets = boto3.client("secretsmanager")
table = boto3.resource("dynamodb").Table("subscriptions")
_ecpay = None  # cached per warm container; changing the secret needs a cold start


class BadRequest(Exception):
    pass


def handler(event, context):
    try:
        email, plan_name, target_price = parse_request(event)
    except BadRequest as err:
        return respond(400, {"message": str(err)})

    ecpay_config()  # fail before writing anything if flight/ecpay is missing or malformed
    plan = PLANS[plan_name]
    route = f"{plan['origin']}-{plan['destination']}"
    now = datetime.now(timezone.utc)

    # A paid plan only changes its target. Anything else goes (back) through the cashier.
    saved = update_paid_target(email, route, target_price, now)
    if saved is None:
        try:
            trade_no = start_checkout(email, route, plan_name, plan, target_price, now)
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            # ReturnURL activated the row between the two writes: it is paid now.
            saved = update_paid_target(email, route, target_price, now)
        else:
            return respond_html(checkout_form(email, route, trade_no, now))
    print(json.dumps({"target_updated": route, "target_price": str(target_price)}))
    return respond(200, saved)


def update_paid_target(email, route, target_price, now):
    """Set the target on an `active` row and leave its payment state alone.
    Returns the updated row, or None if the row isn't active (or doesn't exist)."""
    try:
        return table.update_item(
            Key={"email": email, "route": route},
            UpdateExpression="SET target_price = :target, updated_at = :now",
            ConditionExpression="subscription_status = :active",
            ExpressionAttributeValues={
                ":target": target_price,
                ":now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ":active": "active",
            },
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return None


def billing_period():
    """(PeriodType, Frequency, ExecTimes) from the environment. The default is monthly and
    effectively open-ended; the operator sets D/1/2 to test renewals, then switches back."""
    return (
        os.environ.get("PERIOD_TYPE", "M").strip() or "M",
        int(os.environ.get("FREQUENCY", "1").strip() or 1),
        int(os.environ.get("EXEC_TIMES", "999").strip() or 999),
    )


def start_checkout(email, route, plan_name, plan, target_price, now):
    """Upsert the row as `pending_payment` with a fresh trade number; returns that number.
    Raises ConditionalCheckFailedException if the row has become `active` (never downgrade)."""
    trade_no = new_trade_no(now)
    period_type, frequency, exec_times = billing_period()
    # Upsert on (email, route): overwrite the target, keep the first created_at, and start
    # a fresh checkout attempt. Activation is the ReturnURL callback's job.
    table.update_item(
        Key={"email": email, "route": route},
        ConditionExpression="attribute_not_exists(subscription_status) OR subscription_status <> :active",
        UpdateExpression=(
            "SET plan_name = :plan, origin = :origin, destination = :dest, target_price = :target,"
            " currency = :currency, created_at = if_not_exists(created_at, :now), updated_at = :now,"
            " subscription_status = :pending, merchant_trade_no = :trade_no,"
            " period_type = :period_type, frequency = :frequency, exec_times = :exec_times"
        ),
        ExpressionAttributeValues={
            ":plan": plan_name,
            ":origin": plan["origin"],
            ":dest": plan["destination"],
            ":target": target_price,
            ":currency": "TWD",
            ":now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ":pending": "pending_payment",
            ":trade_no": trade_no,
            ":period_type": period_type,
            ":frequency": frequency,
            ":exec_times": exec_times,
            ":active": "active",
        },
    )
    print(json.dumps({"checkout": route, "merchant_trade_no": trade_no, "target_price": str(target_price)}))
    return trade_no


def new_trade_no(now):
    """Unique per attempt, alphanumeric, exactly 20 chars (ECPay's limit)."""
    return f"FP{now:%y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"


def checkout_form(email, route, trade_no, now):
    cfg = ecpay_config()
    api = os.environ["API_BASE_URL"].strip().rstrip("/")
    period_type, frequency, exec_times = billing_period()
    params = {
        "MerchantID": cfg["merchant_id"],
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": now.astimezone(TAIWAN).strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
        "TotalAmount": cfg["amount"],
        "PeriodAmount": cfg["amount"],
        "PeriodType": period_type,
        "Frequency": str(frequency),
        "ExecTimes": str(exec_times),
        "ItemName": ITEM_NAME,
        "TradeDesc": TRADE_DESC,
        "ReturnURL": f"{api}/ecpay-return",
        "PeriodReturnURL": f"{api}/ecpay-period",
        "OrderResultURL": f"{api}/ecpay-result",
        "CustomField1": email,
        "CustomField2": route,
    }
    params["CheckMacValue"] = check_mac_value(params, cfg["hash_key"], cfg["hash_iv"])
    inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">' for k, v in params.items()
    )
    return (
        '<!doctype html>\n<html><head><meta charset="utf-8"><title>前往綠界付款</title></head><body>\n'
        f'<form method="post" action="{html.escape(cfg["cashier_url"])}">\n{inputs}\n'
        '<noscript><button type="submit">前往綠界付款</button></noscript>\n</form>\n'
        "<script>document.forms[0].submit()</script>\n</body></html>"
    )


def ecpay_config():
    """{merchant_id, hash_key, hash_iv, amount, cashier_url} from flight/ecpay (stripped)."""
    global _ecpay
    if _ecpay is None:
        raw = json.loads(secrets.get_secret_value(SecretId="flight/ecpay")["SecretString"])
        cfg = {k: str(raw[k]).strip() for k in ("merchant_id", "hash_key", "hash_iv", "amount")}
        cfg["cashier_url"] = CASHIER_URLS[str(raw.get("env") or "stage").strip()]
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

    email = body.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        raise BadRequest("email is required.")

    plan_name = body.get("plan_name")
    if plan_name not in PLANS:
        raise BadRequest(f"Unknown plan_name; expected one of: {', '.join(PLANS)}.")

    return email.strip(), plan_name, parse_target_price(body.get("target_price"))


def parse_target_price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BadRequest("target_price must be a positive number (TWD).")
    try:
        price = Decimal(str(value).strip())
    except InvalidOperation:
        raise BadRequest("target_price must be a positive number (TWD).")
    if not price.is_finite() or price <= 0:
        raise BadRequest("target_price must be a positive number (TWD).")
    return price


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def respond_html(page):
    return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": page}


def respond(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, default=_json_default, ensure_ascii=False),
    }


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
