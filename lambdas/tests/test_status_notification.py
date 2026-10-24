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


TRADE_NO = "FP2610241200001A2B3C"
CANCELLED_AT = "2026-11-02T08:00:00Z"


def welcome(email="amy@example.com", route="TPE-TYO", plan="tokyo", trade_no=TRADE_NO):
    return {"event_type": "welcome", "email": email, "route": route, "plan_name": plan, "merchant_trade_no": trade_no}


def cancel(email="amy@example.com", route="TPE-TYO", plan="tokyo", period_end_date="2026-11-24",
           cancelled_at=CANCELLED_AT):
    return {
        "event_type": "cancel", "email": email, "route": route, "plan_name": plan,
        "current_period_end_date": period_end_date, "cancelled_at": cancelled_at,
    }


def subscriber(table, **overrides):
    """The subscriber's row, which records which welcome / cancel email has been sent."""
    item = {"email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
            "subscription_status": "active", "merchant_trade_no": TRADE_NO, **overrides}
    table.put_item(Item=item)
    return item


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
def secrets(ecpay_secret, subscriptions_table):
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


# ------------------------------------------------------- SQS redelivery


@pytest.mark.parametrize("message", [welcome(), cancel()], ids=["welcome", "cancel"])
def test_a_redelivered_status_message_is_emailed_once(monkeypatch, secrets, subscriptions_table, message):
    subscriber(subscriptions_table)
    fn, resend = setup(monkeypatch)

    first = fn.handler(sqs_event(message), None)
    second = fn.handler(sqs_event(message), None)

    assert (first["results"], second["results"]) == (["sent"], ["duplicate"])
    assert len(resend.requests) == 1


def test_a_new_subscription_gets_its_own_welcome(monkeypatch, secrets, subscriptions_table):
    # A re-subscriber's new checkout has a new trade number, so its welcome is not a repeat.
    subscriber(subscriptions_table)
    fn, resend = setup(monkeypatch)
    fn.handler(sqs_event(welcome(trade_no="FP2609011200001OLD00")), None)

    result = fn.handler(sqs_event(welcome()), None)

    assert result["results"] == ["sent"]
    assert len(resend.requests) == 2


def test_a_later_cancel_gets_its_own_email(monkeypatch, secrets, subscriptions_table):
    subscriber(subscriptions_table)
    fn, resend = setup(monkeypatch)
    fn.handler(sqs_event(cancel(cancelled_at="2026-10-02T08:00:00Z")), None)

    result = fn.handler(sqs_event(cancel()), None)

    assert result["results"] == ["sent"]
    assert len(resend.requests) == 2


@pytest.mark.parametrize("message", [welcome(), cancel()], ids=["welcome", "cancel"])
def test_a_failed_send_is_not_recorded_so_the_redelivery_sends_it(monkeypatch, secrets, subscriptions_table, message):
    subscriber(subscriptions_table)
    fn, resend = setup(monkeypatch, failure=503)
    with pytest.raises(Exception):
        fn.handler(sqs_event(message), None)

    resend.failure = None
    result = fn.handler(sqs_event(message), None)

    assert result["results"] == ["sent"]
    assert len(resend.requests) == 2


def test_a_message_without_its_identity_is_still_sent(monkeypatch, secrets, subscriptions_table):
    # Enqueued before the identity fields existed: nothing to dedup on, so send it.
    subscriber(subscriptions_table)
    fn, resend = setup(monkeypatch)
    legacy = {k: v for k, v in welcome().items() if k != "merchant_trade_no"}

    fn.handler(sqs_event(legacy), None)
    fn.handler(sqs_event(legacy), None)

    assert len(resend.requests) == 2
