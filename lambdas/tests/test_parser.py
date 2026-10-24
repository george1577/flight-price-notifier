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

    def subscribe(
        email,
        target,
        route="TPE-TYO",
        plan="tokyo",
        updated_at=None,
        status="active",
        current_period_end=None,
    ):
        """Seed a subscriptions row. `status=None` writes a legacy (pre-M2) row with no status."""
        item = {
            "email": email,
            "route": route,
            "plan_name": plan,
            "origin": "TPE",
            "destination": route.split("-")[1],
            "target_price": Decimal(str(target)),
            "currency": "TWD",
        }
        if updated_at:
            item["updated_at"] = updated_at
        if status is not None:
            item["subscription_status"] = status
        if current_period_end is not None:
            item["current_period_end"] = current_period_end
        subscriptions_table.put_item(Item=item)

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


def test_message_carries_subscription_updated_at_for_the_rearm_check(env, monkeypatch):
    subscribe, messages = env
    subscribe("a@example.com", 12000, updated_at="2026-10-23T13:57:08Z")

    run_parser(monkeypatch, {"twd": provider_body(offer(9325))})

    assert messages()[0]["subscription_updated_at"] == "2026-10-23T13:57:08Z"


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


# --- M2 paywall: only paying subscribers are enqueued -------------------------------

MET = {"twd": provider_body(offer(9325))}  # cheapest 9,325 TWD; every target below is >= it


def test_pending_payment_row_is_not_enqueued_even_when_the_target_is_met(env, monkeypatch):
    subscribe, messages = env
    subscribe("paid@example.com", 12000)
    subscribe("unpaid@example.com", 12000, status="pending_payment")

    result, _ = run_parser(monkeypatch, MET)

    assert [m["email"] for m in messages()] == ["paid@example.com"]
    assert result["matched"] == 1


def stamp(days_from_now):
    """A current_period_end in the stored fixed-width UTC format, offset from today."""
    return (date.today() + timedelta(days=days_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_cancelled_row_inside_its_paid_period_is_still_enqueued(env, monkeypatch):
    subscribe, messages = env
    subscribe("grace@example.com", 12000, status="cancelled", current_period_end=stamp(10))

    run_parser(monkeypatch, MET)

    assert [m["email"] for m in messages()] == ["grace@example.com"]


def test_lapsed_cancelled_row_is_retired_to_expired_and_not_enqueued(env, monkeypatch, subscriptions_table):
    subscribe, messages = env
    subscribe("lapsed@example.com", 12000, status="cancelled", current_period_end=stamp(-1))

    result, _ = run_parser(monkeypatch, MET)

    assert messages() == []
    assert result["matched"] == 0
    row = subscriptions_table.get_item(Key={"email": "lapsed@example.com", "route": "TPE-TYO"})["Item"]
    assert row["subscription_status"] == "expired"


@pytest.mark.parametrize("status", ["expired", None], ids=["expired", "legacy-no-status"])
def test_expired_and_legacy_rows_are_never_enqueued(env, monkeypatch, status):
    subscribe, messages = env
    subscribe("x@example.com", 12000, status=status, current_period_end=stamp(10))

    result, _ = run_parser(monkeypatch, MET)

    assert messages() == []
    assert result["matched"] == 0


def test_retiring_does_not_clobber_a_row_that_was_reactivated_after_the_scan(env, monkeypatch, subscriptions_table):
    subscribe, messages = env
    subscribe("back@example.com", 12000, status="active", current_period_end=stamp(-1))
    stale = dict(
        subscriptions_table.get_item(Key={"email": "back@example.com", "route": "TPE-TYO"})["Item"],
        subscription_status="cancelled",
    )
    monkeypatch.setattr(urllib.request, "urlopen", FakeTravelpayouts(MET))
    parser = load_lambda("flight-parser")
    monkeypatch.setattr(parser, "subscribers", lambda route: iter([stale]))

    parser.handler(ROUTE_EVENT, None)

    row = subscriptions_table.get_item(Key={"email": "back@example.com", "route": "TPE-TYO"})["Item"]
    assert row["subscription_status"] == "active"
