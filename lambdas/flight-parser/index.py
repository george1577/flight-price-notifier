"""flight-parser: handle ONE route per invocation, e.g. {origin, destination, route}.

1. Read the Travelpayouts token from `flight/travelpayouts`; target month = next month.
2. Fetch the cheapest fare in TWD (the gate), then USD (best effort) from /v1/prices/cheap.
3. Scan `subscriptions` for the route; enqueue a self-contained match message to
   `flight-fare-queue` for every row with target_price >= cheapest TWD price.
   M1 has no payment guard, so there is no status filter.

An empty / failed (e.g. 429) TWD response skips the route. A failed USD response only
drops `cheapest_usd` from the message.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

API_URL = "https://api.travelpayouts.com/v1/prices/cheap"
# Some hosts behind Cloudflare reject urllib's default User-Agent with a 403.
USER_AGENT = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
QUEUE_NAME = os.environ.get("QUEUE_NAME", "flight-fare-queue")

secrets = boto3.client("secretsmanager")
sqs = boto3.client("sqs")
table = boto3.resource("dynamodb").Table("subscriptions")
_queue_url = None


def handler(event, context):
    origin = event["origin"]
    destination = event["destination"]
    route = event.get("route") or f"{origin}-{destination}"
    month = next_month(date.today())
    token = read_token()

    twd = fetch_cheapest(origin, destination, month, token, "twd")
    if not twd:
        log(route=route, month=month, cheapest_twd=None, matched=0, note="no TWD fare (empty/error) - skipping")
        return {"ok": True, "route": route, "matched": 0}
    usd = fetch_cheapest(origin, destination, month, token, "usd")  # may be None: never blocks

    matched = 0
    for sub in subscribers(route):
        target = Decimal(str(sub["target_price"]))
        if target < Decimal(str(twd["price"])):
            continue
        body = {
            "email": sub["email"],
            "route": route,
            "plan_name": sub.get("plan_name"),
            "target_price": to_number(target),
            "cheapest": twd,
        }
        if usd:
            body["cheapest_usd"] = usd
        sqs.send_message(QueueUrl=queue_url(), MessageBody=json.dumps(body, ensure_ascii=False))
        matched += 1

    log(
        route=route,
        month=month,
        cheapest_twd=twd["price"],
        airline=twd["airline"],
        cheapest_usd=usd["price"] if usd else None,
        matched=matched,
    )
    return {"ok": True, "route": route, "matched": matched}


def next_month(today: date) -> str:
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return f"{year:04d}-{month:02d}"


def read_token() -> str:
    raw = secrets.get_secret_value(SecretId="flight/travelpayouts")["SecretString"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    return parsed["token"] if isinstance(parsed, dict) else str(parsed)


def fetch_cheapest(origin, destination, month, token, currency):
    """Cheapest offer for the month in `currency`, as a contract fare dict, or None."""
    query = urllib.parse.urlencode(
        {"origin": origin, "destination": destination, "depart_date": month, "currency": currency, "token": token}
    )
    req = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        log(route=f"{origin}-{destination}", currency=currency, http_status=err.code, note="fetch failed")
        return None
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        log(route=f"{origin}-{destination}", currency=currency, error=str(err), note="fetch failed")
        return None

    offers = (body.get("data") or {}).get(destination) if body.get("success") else None
    if not offers:
        return None
    candidates = offers.values() if isinstance(offers, dict) else offers
    priced = [o for o in candidates if isinstance(o, dict) and o.get("price") is not None]
    if not priced:
        return None
    best = min(priced, key=lambda o: o["price"])
    # The provider's real keys are departure_at / return_at, not depart_date / return_date.
    return {
        "price": best["price"],
        "currency": currency.upper(),
        "airline": best.get("airline"),
        "depart_date": best.get("departure_at"),
        "return_date": best.get("return_at"),
    }


def subscribers(route):
    kwargs = {"FilterExpression": Attr("route").eq(route)}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def queue_url():
    global _queue_url
    if _queue_url is None:
        _queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    return _queue_url


def to_number(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


def log(**fields):
    print(json.dumps({"fn": "flight-parser", **fields}, ensure_ascii=False, default=str))
