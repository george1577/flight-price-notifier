"""flight-list-subscriptions: GET /subscriptions?email=... -> that user's `subscriptions` rows.

KNOWN GAP (accepted for M1): the API trusts the email the client sends. There is no
Supabase JWT check, so anyone who knows an address can list (or, via POST /subscribe,
overwrite) its subscriptions. Verifying the caller's token is deferred past M1.
"""

import json
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

table = boto3.resource("dynamodb").Table(os.environ.get("SUBSCRIPTIONS_TABLE", "subscriptions"))


def handler(event, context):
    email = ((event.get("queryStringParameters") or {}).get("email") or "").strip()
    if not EMAIL_RE.match(email):
        return respond(400, {"message": "email query parameter is required."})

    items, kwargs = [], {"KeyConditionExpression": Key("email").eq(email)}
    while True:
        page = table.query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    print(json.dumps({"listed": len(items)}))
    return respond(200, items)


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
