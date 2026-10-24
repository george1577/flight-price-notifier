"""flight-save-subscription: POST /subscribe {email, plan_name, target_price}.

Upserts one `subscriptions` row per (email, route). M1 has no payment guard, so the
row carries no subscription_status or any other payment field.
"""

import base64
import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3

PLANS = {
    "tokyo": {"origin": "TPE", "destination": "TYO"},
    "seoul": {"origin": "TPE", "destination": "SEL"},
    "london": {"origin": "TPE", "destination": "LON"},
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

table = boto3.resource("dynamodb").Table("subscriptions")


class BadRequest(Exception):
    pass


def handler(event, context):
    try:
        email, plan_name, target_price = parse_request(event)
    except BadRequest as err:
        return respond(400, {"message": str(err)})

    plan = PLANS[plan_name]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Upsert on (email, route): overwrite the target, keep the first created_at.
    saved = table.update_item(
        Key={"email": email, "route": f"{plan['origin']}-{plan['destination']}"},
        UpdateExpression=(
            "SET plan_name = :plan, origin = :origin, destination = :dest, target_price = :target,"
            " currency = :currency, created_at = if_not_exists(created_at, :now), updated_at = :now"
        ),
        ExpressionAttributeValues={
            ":plan": plan_name,
            ":origin": plan["origin"],
            ":dest": plan["destination"],
            ":target": target_price,
            ":currency": "TWD",
            ":now": now,
        },
        ReturnValues="ALL_NEW",
    )["Attributes"]
    print(json.dumps({"saved": saved["route"], "target_price": str(target_price)}))
    return respond(200, saved)


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
