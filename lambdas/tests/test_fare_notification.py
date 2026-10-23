"""flight-fare-notification: SQS match message -> Resend email -> notification_history row.

Resend is stubbed at urllib.request.urlopen; AWS (Secrets Manager, DynamoDB) is faked
with moto.
"""

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from boto3.dynamodb.conditions import Key

from conftest import REGION, load_lambda

TWD = {
    "price": 9325,
    "currency": "TWD",
    "airline": "LJ",
    "depart_date": "2026-11-12T08:15:00+08:00",
    "return_date": "2026-11-18T19:40:00+09:00",
}
USD = {**TWD, "price": 298, "currency": "USD"}


def match(email="me@example.com", usd=None, cheapest=TWD, route="TPE-TYO", plan="tokyo", target=12000):
    body = {"email": email, "route": route, "plan_name": plan, "target_price": target, "cheapest": cheapest}
    if usd:
        body["cheapest_usd"] = usd
    return body


def sqs_event(*bodies):
    return {
        "Records": [
            {
                "messageId": f"m{i}",
                "eventSource": "aws:sqs",
                "body": json.dumps(b, ensure_ascii=False),
            }
            for i, b in enumerate(bodies)
        ]
    }


class FakeResend:
    """Stands in for urllib.request.urlopen. `status` is 200 or an HTTP error code."""

    def __init__(self, status=200):
        self.status = status
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.status >= 300:
            raise urllib.error.HTTPError(
                req.full_url, self.status, "stub", {}, io.BytesIO(b'{"message":"stub error"}')
            )
        return io.BytesIO(json.dumps({"id": "email-123"}).encode())

    def sent(self):
        return [json.loads(r.data) for r in self.requests]


@pytest.fixture
def secrets(aws):
    client = boto3.client("secretsmanager", region_name=REGION)
    client.create_secret(
        Name="flight/resend", SecretString=json.dumps({"api_key": "re_test", "from": "onboarding@resend.dev"})
    )
    client.create_secret(Name="flight/travelpayouts", SecretString=json.dumps({"token": "tp-token"}))
    return client


def setup(monkeypatch, status=200):
    fake = FakeResend(status)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return load_lambda("flight-fare-notification"), fake


def history(table, email="me@example.com", route="TPE-TYO"):
    return table.query(KeyConditionExpression=Key("pk").eq(f"{email}#{route}"))["Items"]


def test_first_send_posts_to_resend_and_writes_history(monkeypatch, secrets, notification_history_table):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match()), None)

    assert len(resend.requests) == 1
    req = resend.requests[0]
    assert req.full_url == "https://api.resend.com/emails"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer re_test"
    assert "python-urllib" not in req.get_header("User-agent").lower()
    sent = resend.sent()[0]
    assert sent["from"] == "onboarding@resend.dev"
    assert sent["to"] == ["me@example.com"]

    rows = history(notification_history_table)
    assert len(rows) == 1
    row = rows[0]
    assert row["pk"] == "me@example.com#TPE-TYO"
    assert row["email"] == "me@example.com"
    assert row["route"] == "TPE-TYO"
    assert row["price"] == 9325
    assert row["currency"] == "TWD"
    assert row["sent_at"].endswith("Z")  # ISO-8601 UTC string, not an epoch number
    sent_at = datetime.fromisoformat(row["sent_at"].replace("Z", "+00:00"))
    assert abs(datetime.now(timezone.utc) - sent_at) < timedelta(minutes=1)


def test_email_leads_with_ntd_and_always_has_a_text_part(monkeypatch, secrets, notification_history_table):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match()), None)

    sent = resend.sent()[0]
    assert sent["subject"] == "✈️ 台北 → 東京 降價通知！NT$9,325 已達標"
    html = sent["html"]
    assert "NT$9,325" in html
    assert "NT$12,000" in html  # the target price
    assert "LJ" in html
    assert "2026-11-12" in html and "2026-11-18" in html
    assert "立即訂購" in html
    assert 'href="https://www.aviasales.com/search/TPE1211TYO1811"' in html
    assert "<table" not in html.lower()
    assert "US$" not in html  # no cheapest_usd in this message

    text = sent["text"]
    assert text.strip()
    assert "NT$9,325" in text and "NT$12,000" in text
    assert "https://www.aviasales.com/search/TPE1211TYO1811" in text
    assert "US$" not in text


def test_usd_line_appears_only_when_message_has_cheapest_usd(monkeypatch, secrets, notification_history_table):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match(usd=USD)), None)

    sent = resend.sent()[0]
    assert "約 US$298" in sent["html"]
    assert "約 US$298" in sent["text"]
    assert sent["subject"].startswith("✈️ 台北 → 東京") and "NT$9,325" in sent["subject"]


def test_seoul_route_uses_seoul_in_subject_and_link(monkeypatch, secrets, notification_history_table):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match(route="TPE-SEL", plan="seoul")), None)

    sent = resend.sent()[0]
    assert sent["subject"] == "✈️ 台北 → 首爾 降價通知！NT$9,325 已達標"
    assert "https://www.aviasales.com/search/TPE1211SEL1811" in sent["html"]


def test_booking_link_gets_marker_only_when_configured(monkeypatch, secrets, notification_history_table):
    secrets.put_secret_value(
        SecretId="flight/travelpayouts", SecretString=json.dumps({"token": "tp-token", "marker": "736582"})
    )
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match()), None)

    sent = resend.sent()[0]
    assert "https://www.aviasales.com/search/TPE1211TYO1811?marker=736582" in sent["html"]
    assert "https://www.aviasales.com/search/TPE1211TYO1811?marker=736582" in sent["text"]


def test_one_way_fare_links_without_return_part(monkeypatch, secrets, notification_history_table):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(match(cheapest={**TWD, "return_date": None})), None)

    assert 'href="https://www.aviasales.com/search/TPE1211TYO"' in resend.sent()[0]["html"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_resend_failure_raises_so_sqs_redelivers(monkeypatch, secrets, notification_history_table, status):
    fn, resend = setup(monkeypatch, status=status)

    with pytest.raises(Exception):
        fn.handler(sqs_event(match()), None)

    assert len(resend.requests) == 1
    assert history(notification_history_table) == []


@pytest.mark.parametrize("status", [403, 422])
def test_permanent_resend_failure_is_dropped_without_history(
    monkeypatch, secrets, notification_history_table, capsys, status
):
    fn, resend = setup(monkeypatch, status=status)

    fn.handler(sqs_event(match()), None)  # returns normally: SQS deletes the message

    assert len(resend.requests) == 1
    assert history(notification_history_table) == []
    assert str(status) in capsys.readouterr().out
