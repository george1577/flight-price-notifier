"""Every Lambda finds its table and secrets through the environment, so one template can
deploy an isolated dev stack next to prod (infra/flight-m1.yaml `Env`)."""

import re

import boto3
import pytest

from conftest import LAMBDAS_DIR, REGION, api_event, load_lambda, response_json

LAMBDA_SOURCES = sorted(LAMBDAS_DIR.glob("flight-*/index.py"))


@pytest.mark.parametrize("path", LAMBDA_SOURCES, ids=lambda p: p.parent.name)
def test_no_lambda_hard_codes_a_table_or_secret_name(path):
    source = path.read_text()

    assert not re.search(r'\.Table\(\s*"', source), "read table names from the environment"
    assert not re.search(r'SecretId\s*=\s*"', source), "build secret ids from SECRETS_PREFIX"


def test_a_dev_stack_reads_its_own_table(aws, monkeypatch):
    monkeypatch.setenv("SUBSCRIPTIONS_TABLE", "subscriptions-dev")
    ddb = boto3.resource("dynamodb", region_name=REGION)
    for name in ("subscriptions", "subscriptions-dev"):
        ddb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}, {"AttributeName": "route", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "email", "AttributeType": "S"},
                {"AttributeName": "route", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    ddb.Table("subscriptions").put_item(Item={"email": "amy@example.com", "route": "TPE-TYO", "env": "prod"})
    ddb.Table("subscriptions-dev").put_item(Item={"email": "amy@example.com", "route": "TPE-TYO", "env": "dev"})

    handler = load_lambda("flight-list-subscriptions").handler
    response = handler(api_event("GET", "/subscriptions", query={"email": "amy@example.com"}), None)

    assert [row["env"] for row in response_json(response)] == ["dev"]
