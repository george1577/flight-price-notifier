"""flight-parser: one route per invocation -> cheapest fare -> SQS match messages.

Travelpayouts is stubbed at urllib.request.urlopen; AWS (Secrets Manager, DynamoDB,
SQS) is faked with moto.
"""

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

import boto3
import pytest

from conftest import REGION, load_lambda

ROUTE_EVENT = {"origin": "TPE", "destination": "TYO", "route": "TPE-TYO"}


def provider_body(*offers, dest="TYO"):
    """A /v1/prices/cheap response, using the provider's real keys."""
    return {
        "success": True,
        "data": {dest: {str(i): offer for i, offer in enumerate(offers)}} if offers else {},
        "currency": "twd",
    }


def offer(price, airline="LJ", depart="2026-11-12T08:15:00+08:00", ret="2026-11-18T19:40:00+09:00"):
    return {
        "price": price,
        "airline": airline,
        "flight_number": 712,
        "departure_at": depart,
        "return_at": ret,
        "expires_at": "2026-10-24T00:00:00Z",
    }


class FakeTravelpayouts:
    """Stands in for urllib.request.urlopen. `responses` maps currency -> body dict or HTTP status."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        result = self.responses.get(query["currency"], 429)
        if isinstance(result, int):
            raise urllib.error.HTTPError(req.full_url, result, "stub", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(result).encode())


@pytest.fixture
def env(aws, subscriptions_table):
    boto3.client("secretsmanager", region_name=REGION).create_secret(
        Name="flight/travelpayouts", SecretString=json.dumps({"token": "tp-token"})
    )
    sqs = boto3.client("sqs", region_name=REGION)
    queue_url = sqs.create_queue(QueueName="flight-fare-queue")["QueueUrl"]

    def subscribe(email, target, route="TPE-TYO", plan="tokyo"):
        subscriptions_table.put_item(
            Item={
                "email": email,
                "route": route,
                "plan_name": plan,
                "origin": "TPE",
                "destination": route.split("-")[1],
                "target_price": Decimal(str(target)),
                "currency": "TWD",
            }
        )

    def messages():
        out = []
        while True:
            got = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
            if not got:
                return out
            for m in got:
                out.append(json.loads(m["Body"]))
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])

    return subscribe, messages


def run_parser(monkeypatch, responses, event=ROUTE_EVENT):
    fake = FakeTravelpayouts(responses)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    parser = load_lambda("flight-parser")
    result = parser.handler(event, None)
    return result, fake


def test_parses_real_provider_keys_into_the_queue_contract(env, monkeypatch):
    subscribe, messages = env
    subscribe("a@example.com", 12000)

    run_parser(
        monkeypatch,
        {
            "twd": provider_body(offer(9800, "BR"), offer(9325, "LJ")),
            "usd": provider_body(offer(298, "IT", "2026-11-03T10:00:00+08:00", "2026-11-09T12:00:00+09:00")),
        },
    )

    assert messages() == [
        {
            "email": "a@example.com",
            "route": "TPE-TYO",
            "plan_name": "tokyo",
            "target_price": 12000,
            "cheapest": {
                "price": 9325,
                "currency": "TWD",
                "airline": "LJ",
                "depart_date": "2026-11-12T08:15:00+08:00",
                "return_date": "2026-11-18T19:40:00+09:00",
            },
            "cheapest_usd": {
                "price": 298,
                "currency": "USD",
                "airline": "IT",
                "depart_date": "2026-11-03T10:00:00+08:00",
                "return_date": "2026-11-09T12:00:00+09:00",
            },
        }
    ]


def test_calls_cheap_endpoint_for_next_month_with_token_and_custom_user_agent(env, monkeypatch):
    _, _ = env
    _, fake = run_parser(monkeypatch, {"twd": provider_body(offer(9325)), "usd": provider_body(offer(298))})

    req = fake.requests[0]
    parts = urllib.parse.urlsplit(req.full_url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert parts.netloc == "api.travelpayouts.com"
    assert parts.path == "/v1/prices/cheap"
    assert query["origin"] == "TPE"
    assert query["destination"] == "TYO"
    assert query["token"] == "tp-token"
    assert query["currency"] == "twd"
    # Next month, derived independently: day 28 + 4 days always lands in the next month.
    assert query["depart_date"] == (date.today().replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
    ua = req.get_header("User-agent")
    assert ua and "Python-urllib" not in ua


def test_target_equal_to_cheapest_matches_and_below_does_not(env, monkeypatch):
    subscribe, messages = env
    subscribe("equal@example.com", 9325)
    subscribe("above@example.com", 15000)
    subscribe("below@example.com", 9324)
    subscribe("seoul@example.com", 99999, route="TPE-SEL", plan="seoul")

    result, _ = run_parser(monkeypatch, {"twd": provider_body(offer(9325)), "usd": provider_body(offer(298))})

    assert sorted(m["email"] for m in messages()) == ["above@example.com", "equal@example.com"]
    assert result["matched"] == 2


@pytest.mark.parametrize("twd_response", [provider_body(), {"success": False, "data": {}}, 429])
def test_no_twd_fare_skips_the_route_without_messages(env, monkeypatch, twd_response):
    subscribe, messages = env
    subscribe("a@example.com", 99999)

    result, _ = run_parser(monkeypatch, {"twd": twd_response, "usd": provider_body(offer(298))})

    assert messages() == []
    assert result["matched"] == 0


@pytest.mark.parametrize("usd_response", [provider_body(), 429])
def test_usd_failure_only_drops_cheapest_usd(env, monkeypatch, usd_response):
    subscribe, messages = env
    subscribe("a@example.com", 12000)

    run_parser(monkeypatch, {"twd": provider_body(offer(9325)), "usd": usd_response})

    [message] = messages()
    assert "cheapest_usd" not in message
    assert message["cheapest"]["price"] == 9325
    assert message["cheapest"]["currency"] == "TWD"


def test_logs_cheapest_fare_and_match_count(env, monkeypatch, capsys):
    subscribe, _ = env
    subscribe("a@example.com", 12000)

    run_parser(monkeypatch, {"twd": provider_body(offer(9325)), "usd": provider_body(offer(298))})

    out = capsys.readouterr().out
    assert "TPE-TYO" in out and "9325" in out and '"matched": 1' in out
