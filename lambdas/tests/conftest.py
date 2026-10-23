"""Shared pytest harness for the Lambdas under lambdas/<function-name>/index.py.

Every Lambda module is called `index`, so `load_lambda()` imports each one under a
unique module name. AWS is faked with moto: the `aws` fixture starts `mock_aws()` and
sets fake credentials + us-east-1. Handlers create boto3 clients at import time, so
load them *after* the `aws` fixture (and any table fixtures) are active.
"""

import importlib.util
import json
import sys
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
