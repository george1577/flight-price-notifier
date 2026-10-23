"""flight-parser-wrapper: read flight-routes.json from S3, async-invoke flight-parser per route."""

import json

import boto3
import pytest

from conftest import LAMBDAS_DIR, REGION, load_lambda

BUCKET = "flight-config-123456789012"
ROUTES = [
    {"plan": "tokyo", "origin": "TPE", "destination": "TYO"},
    {"plan": "seoul", "origin": "TPE", "destination": "SEL"},
]


class FakeLambda:
    """Records invokes; moto can't run the real parser without Docker."""

    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"StatusCode": 202}


@pytest.fixture
def wrapper(aws, monkeypatch):
    monkeypatch.setenv("CONFIG_BUCKET", BUCKET)
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET)
    s3.put_object(Bucket=BUCKET, Key="flight-routes.json", Body=json.dumps(ROUTES))
    module = load_lambda("flight-parser-wrapper")
    module.lambda_client = FakeLambda()
    return module


def test_invokes_parser_once_per_configured_route_asynchronously(wrapper):
    result = wrapper.handler({"source": "aws.events"}, None)

    calls = wrapper.lambda_client.calls
    assert [c["FunctionName"] for c in calls] == ["flight-parser", "flight-parser"]
    assert all(c["InvocationType"] == "Event" for c in calls)
    assert [json.loads(c["Payload"]) for c in calls] == [
        {"origin": "TPE", "destination": "TYO", "route": "TPE-TYO"},
        {"origin": "TPE", "destination": "SEL", "route": "TPE-SEL"},
    ]
    assert result["invoked"] == 2


def test_committed_routes_file_matches_spec():
    committed = json.loads((LAMBDAS_DIR.parent / "infra" / "flight-routes.json").read_text())
    assert committed == ROUTES
