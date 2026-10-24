"""Shared pytest harness for the Lambdas under lambdas/<function-name>/index.py.

Every Lambda module is called `index`, so `load_lambda()` imports each one under a
unique module name. AWS is faked with moto: the `aws` fixture starts `mock_aws()` and
sets fake credentials + us-east-1. Handlers create boto3 clients at import time, so
load them *after* the `aws` fixture (and any table fixtures) are active.
"""

import hashlib
import importlib.util
import json
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

LAMBDAS_DIR = Path(__file__).resolve().parent.parent
REGION = "us-east-1"


def load_lambda(function_name: str):
    """Import lambdas/<function_name>/index.py fresh, as its own module."""
    path = LAMBDAS_DIR / function_name / "index.py"
    module_name = f"lambda_{function_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def aws(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
    }.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        yield


@pytest.fixture
def subscriptions_table(aws):
    """The `subscriptions` table, keyed as in infra/flight-m1.yaml."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.create_table(
        TableName="subscriptions",
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
            {"AttributeName": "route", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "route", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def notification_history_table(aws):
    """The `notification_history` table, keyed as in infra/flight-m1.yaml."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.create_table(
        TableName="notification_history",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sent_at", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sent_at", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


# ECPay's public stage test merchant (safe to commit), as stored in the flight/ecpay secret.
ECPAY_STAGE = {
    "merchant_id": "3002607",
    "hash_key": "pwFHCqoQZGmho4w6",
    "hash_iv": "EkRm7iFT261dpevs",
    "env": "stage",
    "amount": "300",
}


@pytest.fixture
def ecpay_secret(aws):
    """The flight/ecpay secret (created in the prerequisites, not by the stack)."""
    client = boto3.client("secretsmanager", region_name=REGION)
    client.create_secret(Name="flight/ecpay", SecretString=json.dumps(ECPAY_STAGE))
    return client


def ecpay_cmv(params: dict, hash_key=ECPAY_STAGE["hash_key"], hash_iv=ECPAY_STAGE["hash_iv"]) -> str:
    """The tests' own CheckMacValue, written straight from ECPay's spec, so tests can sign
    callbacks and check forms without trusting a Lambda's copy. Also checked against
    ECPay's vectors in test_checkmacvalue.py."""
    pairs = sorted(((k, v) for k, v in params.items() if k != "CheckMacValue"), key=lambda kv: kv[0].lower())
    raw = "&".join([f"HashKey={hash_key}", *(f"{k}={v}" for k, v in pairs), f"HashIV={hash_iv}"])
    encoded = urllib.parse.quote_plus(raw).replace("~", "%7e").lower()
    for char in "-_.!*()":
        encoded = encoded.replace(f"%{ord(char):02x}", char)
    return hashlib.sha256(encoded.encode()).hexdigest().upper()


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = None
        self.fields = {}
        self.scripts = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and self.form is None:
            self.form = attrs
        elif tag == "input" and attrs.get("type") == "hidden":
            self.fields[attrs["name"]] = attrs.get("value", "")
        elif tag == "script":
            self._in_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)


def parse_checkout_form(html: str) -> dict:
    """Read an auto-submit checkout page the way the browser would:
    {"action", "method", "fields": {name: value}, "scripts": [...], "html"}."""
    parser = _FormParser()
    parser.feed(html)
    form = parser.form or {}
    return {
        "action": form.get("action"),
        "method": (form.get("method") or "").lower(),
        "fields": parser.fields,
        "scripts": parser.scripts,
        "html": html,
    }


def api_event(method: str, path: str, body=None, query=None) -> dict:
    """An API Gateway HTTP API (payload format 2.0) proxy event."""
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "queryStringParameters": query,
        "requestContext": {"http": {"method": method, "path": path}},
        "body": body if body is None or isinstance(body, str) else json.dumps(body),
        "isBase64Encoded": False,
    }


def response_json(response: dict):
    """Parse a handler response body the way the browser would (plain JSON)."""
    return json.loads(response["body"])
