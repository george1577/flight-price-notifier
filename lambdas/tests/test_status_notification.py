"""flight-status-notification: SQS status message (welcome / cancel) -> one Resend email.

Resend is stubbed at urllib.request.urlopen; Secrets Manager is faked with moto. The
monthly price comes from the flight/ecpay secret (`amount`), the same one the checkout uses.
"""

import io
import json
import urllib.error
import urllib.request

import pytest

from conftest import REGION, load_lambda


def sqs_event(*bodies):
    return {
        "Records": [
            {"messageId": f"m{i}", "eventSource": "aws:sqs", "body": json.dumps(b, ensure_ascii=False)}
            for i, b in enumerate(bodies)
        ]
    }


def welcome(email="amy@example.com", route="TPE-TYO", plan="tokyo"):
    return {"event_type": "welcome", "email": email, "route": route, "plan_name": plan}


def cancel(email="amy@example.com", route="TPE-TYO", plan="tokyo", period_end_date="2026-11-24"):
    return {
        "event_type": "cancel", "email": email, "route": route, "plan_name": plan,
        "current_period_end_date": period_end_date,
    }


class FakeResend:
    """Stands in for urllib.request.urlopen. `failure` is an HTTP status code or an exception."""

    def __init__(self, failure=None):
        self.failure = failure
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure:
            raise urllib.error.HTTPError(
                req.full_url, self.failure, "stub", {}, io.BytesIO(b'{"message":"stub error"}')
            )
        return io.BytesIO(json.dumps({"id": "email-123"}).encode())

    def sent(self):
        return [json.loads(r.data) for r in self.requests]


@pytest.fixture
def secrets(ecpay_secret):
    ecpay_secret.create_secret(
        Name="flight/resend", SecretString=json.dumps({"api_key": "re_test", "from": "onboarding@resend.dev"})
    )
    return ecpay_secret


def setup(monkeypatch, failure=None):
    fake = FakeResend(failure)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return load_lambda("flight-status-notification"), fake


# ------------------------------------------------------------------ welcome


def test_welcome_posts_one_email_to_resend_like_the_fare_alert(monkeypatch, secrets):
    fn, resend = setup(monkeypatch)

    result = fn.handler(sqs_event(welcome()), None)

    assert result["results"] == ["sent"]
    assert len(resend.requests) == 1
    req = resend.requests[0]
    assert req.full_url == "https://api.resend.com/emails"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer re_test"
    assert "python-urllib" not in req.get_header("User-agent").lower()
    sent = resend.sent()[0]
    assert sent["from"] == "onboarding@resend.dev"
    assert sent["to"] == ["amy@example.com"]


def test_welcome_names_the_route_and_the_monthly_price_in_html_and_text(monkeypatch, secrets):
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(welcome(route="TPE-SEL", plan="seoul")), None)

    sent = resend.sent()[0]
    assert "台北 → 首爾" in sent["subject"]
    for part in (sent["html"], sent["text"]):
        assert "台北 → 首爾" in part
        assert "NT$300" in part
        assert "每月" in part
    assert "<" not in sent["text"]


def test_welcome_uses_the_price_in_the_ecpay_secret(monkeypatch, secrets):
    secrets.put_secret_value(
        SecretId="flight/ecpay",
        SecretString=json.dumps({"merchant_id": "3002607", "hash_key": "k", "hash_iv": "i", "amount": " 1200 "}),
    )
    fn, resend = setup(monkeypatch)

    fn.handler(sqs_event(welcome()), None)

    assert "NT$1,200" in resend.sent()[0]["text"]


# ------------------------------------------------------------------- cancel


def test_cancel_names_the_route_and_the_paid_through_date_in_html_and_text(monkeypatch, secrets):
    fn, resend = setup(monkeypatch)

    result = fn.handler(sqs_event(cancel(route="TPE-LON", plan="london", period_end_date="2026-11-24")), None)

    assert result["results"] == ["sent"]
    sent = resend.sent()[0]
    assert sent["to"] == ["amy@example.com"]
    assert "台北 → 倫敦" in sent["subject"]
    for part in (sent["html"], sent["text"]):
        assert "台北 → 倫敦" in part
        assert "有效至 2026-11-24" in part
    assert "<" not in sent["text"]


# ------------------------------------------------------- unknown event_type


@pytest.mark.parametrize("event_type", ["renewal", "", None])
def test_an_unknown_event_type_is_logged_and_dropped_without_sending(monkeypatch, secrets, capsys, event_type):
    fn, resend = setup(monkeypatch)

    result = fn.handler(sqs_event({**welcome(), "event_type": event_type}), None)  # returns: SQS deletes it

    assert result["results"] == ["dropped"]
    assert resend.requests == []
    assert "unknown event_type" in capsys.readouterr().out


# ------------------------------------------------------------ retry vs drop


@pytest.mark.parametrize(
    "failure",
    [429, 500, 503, urllib.error.URLError("down")],
    ids=["429", "500", "503", "network"],
)
@pytest.mark.parametrize("message", [welcome(), cancel()], ids=["welcome", "cancel"])
def test_a_transient_resend_failure_raises_so_sqs_redelivers(monkeypatch, secrets, message, failure):
    fn, resend = setup(monkeypatch, failure=failure)

    with pytest.raises(Exception):
        fn.handler(sqs_event(message), None)

    assert len(resend.requests) == 1


@pytest.mark.parametrize("status", [403, 422])
@pytest.mark.parametrize("message", [welcome(), cancel()], ids=["welcome", "cancel"])
def test_a_permanent_resend_failure_is_logged_and_dropped(monkeypatch, secrets, capsys, message, status):
    fn, resend = setup(monkeypatch, failure=status)

    result = fn.handler(sqs_event(message), None)  # returns normally: SQS deletes the message

    assert result["results"] == ["dropped"]
    assert len(resend.requests) == 1
    assert str(status) in capsys.readouterr().out
