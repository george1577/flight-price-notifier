"""flight-cancel-subscription: POST /cancel {email, route}.

ECPay's CreditCardPeriodAction endpoint is stubbed at urllib.request.urlopen; the stub
records each request and answers with a URL-encoded body the way ECPay does.
"""

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3
import pytest

from conftest import REGION, api_event, ecpay_cmv, load_lambda, response_json

TRADE_NO = "FP2610241200001A2B3C"
CANCEL_URL = "https://payment-stage.ecpay.com.tw/Cashier/CreditCardPeriodAction"


class FakeEcpay:
    """Answers CreditCardPeriodAction with `rtn_code`, or fails with `error`."""

    def __init__(self, rtn_code="1", error=None):
        self.rtn_code = rtn_code
        self.error = error
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        sent = dict(urllib.parse.parse_qsl(req.data.decode(), keep_blank_values=True))
        reply = {
            "MerchantID": sent.get("MerchantID", ""),
            "MerchantTradeNo": sent.get("MerchantTradeNo", ""),
            "RtnCode": self.rtn_code,
            "RtnMsg": "成功" if self.rtn_code == "1" else "不存在的訂單編號",
        }
        reply["CheckMacValue"] = ecpay_cmv(reply)
        return io.BytesIO(urllib.parse.urlencode(reply).encode())

    def sent_fields(self):
        (req,) = self.requests
        return dict(urllib.parse.parse_qsl(req.data.decode(), keep_blank_values=True))


@pytest.fixture
def status_queue(aws):
    """flight-status-queue; `drain()` returns (and deletes) every message body on it."""
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName="flight-status-queue")["QueueUrl"]

    def drain():
        bodies = []
        while True:
            got = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
            if not got:
                return bodies
            for m in got:
                bodies.append(json.loads(m["Body"]))
                sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])

    return drain


@pytest.fixture
def ecpay(monkeypatch):
    fake = FakeEcpay()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


@pytest.fixture
def module(subscriptions_table, ecpay_secret, status_queue, ecpay):
    return load_lambda("flight-cancel-subscription")


@pytest.fixture
def cancel(module):
    return lambda body: module.handler(api_event("POST", "/cancel", body), None)


def freeze_clock(monkeypatch, module, iso):
    fixed = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)


def active_row(table, **overrides):
    item = {
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 10000, "currency": "TWD",
        "created_at": "2026-10-24T03:00:00Z", "updated_at": "2026-10-24T03:00:00Z",
        "subscription_status": "active", "merchant_trade_no": TRADE_NO,
        "period_type": "M", "frequency": 1, "activated_at": "2026-10-24T03:05:00Z",
        "current_period_end": "2026-11-24T03:05:00Z", "current_period_end_date": "2026-11-24",
    }
    item.update(overrides)
    for key in [k for k, v in item.items() if v is None]:
        del item[key]
    table.put_item(Item=item)
    return item


def get_row(table, email="amy@example.com", route="TPE-TYO"):
    return table.get_item(Key={"email": email, "route": route}).get("Item")


AMY = {"email": "amy@example.com", "route": "TPE-TYO"}


def test_sends_a_signed_cancel_for_the_rows_trade_number(subscriptions_table, ecpay, cancel):
    active_row(subscriptions_table)

    cancel(AMY)

    (req,) = ecpay.requests
    assert req.full_url == CANCEL_URL
    assert req.get_method() == "POST"
    fields = ecpay.sent_fields()
    assert {k: fields[k] for k in ("MerchantID", "MerchantTradeNo", "Action")} == {
        "MerchantID": "3002607", "MerchantTradeNo": TRADE_NO, "Action": "Cancel",
    }
    assert abs(int(fields["TimeStamp"]) - datetime.now(timezone.utc).timestamp()) < 60
    assert fields["CheckMacValue"] == ecpay_cmv(fields)
    assert set(fields) == {"MerchantID", "MerchantTradeNo", "Action", "TimeStamp", "CheckMacValue"}


def test_cancels_the_row_but_keeps_its_paid_period(monkeypatch, module, subscriptions_table, cancel):
    active_row(subscriptions_table)
    freeze_clock(monkeypatch, module, "2026-11-02T08:00:00")

    response = cancel(AMY)

    assert response["statusCode"] == 200
    assert response["headers"]["content-type"] == "application/json"
    row = response_json(response)
    assert row["subscription_status"] == "cancelled"
    assert row["cancelled_at"] == "2026-11-02T08:00:00Z"
    assert row["current_period_end"] == "2026-11-24T03:05:00Z"
    assert row["current_period_end_date"] == "2026-11-24"
    assert row["merchant_trade_no"] == TRADE_NO
    stored = get_row(subscriptions_table)
    assert stored["subscription_status"] == "cancelled"
    assert stored["current_period_end"] == "2026-11-24T03:05:00Z"


def test_enqueues_one_cancel_email_with_the_paid_through_date(subscriptions_table, status_queue, cancel):
    active_row(subscriptions_table)

    cancel(AMY)

    cancelled_at = get_row(subscriptions_table)["cancelled_at"]  # identifies this cancel for the consumer's dedup
    assert status_queue() == [{
        "event_type": "cancel", "email": "amy@example.com", "route": "TPE-TYO",
        "plan_name": "tokyo", "current_period_end_date": "2026-11-24", "cancelled_at": cancelled_at,
    }]


@pytest.mark.parametrize("now, period_end", [
    ("2026-11-02T08:00:00", "2026-12-02T08:00:00Z"),
    ("2027-01-31T08:00:00", "2027-02-28T08:00:00Z"),  # clamped to the month's last day
])
def test_a_row_without_a_period_end_gets_one_month_from_now(
    monkeypatch, module, subscriptions_table, status_queue, cancel, now, period_end
):
    active_row(subscriptions_table, current_period_end=None, current_period_end_date=None)
    freeze_clock(monkeypatch, module, now)

    row = response_json(cancel(AMY))

    assert row["subscription_status"] == "cancelled"
    assert row["current_period_end"] == period_end
    assert row["current_period_end_date"] == period_end[:10]
    assert get_row(subscriptions_table)["current_period_end"] == period_end
    assert status_queue()[0]["current_period_end_date"] == period_end[:10]


@pytest.mark.parametrize("rtn_code", ["90100150", "90100149"])  # no such order / already stopped
def test_an_order_ecpay_no_longer_bills_still_cancels_locally(
    subscriptions_table, status_queue, ecpay, cancel, rtn_code
):
    active_row(subscriptions_table)
    ecpay.rtn_code = rtn_code

    response = cancel(AMY)

    assert response["statusCode"] == 200
    assert get_row(subscriptions_table)["subscription_status"] == "cancelled"
    assert [m["event_type"] for m in status_queue()] == ["cancel"]


@pytest.mark.parametrize("error, rtn_code", [
    (urllib.error.URLError("connection refused"), "1"),
    (urllib.error.HTTPError(CANCEL_URL, 500, "stub", {}, io.BytesIO(b"oops")), "1"),
    (urllib.error.HTTPError(CANCEL_URL, 503, "stub", {}, io.BytesIO(b"busy")), "1"),
    (None, "10200073"),  # ECPay refused the request (CheckMacValue Error): it still bills
])
def test_when_ecpay_does_not_stop_the_order_nothing_changes_locally(
    subscriptions_table, status_queue, ecpay, cancel, error, rtn_code
):
    before = active_row(subscriptions_table)
    ecpay.error, ecpay.rtn_code = error, rtn_code

    response = cancel(AMY)

    assert response["statusCode"] == 502
    assert response["headers"]["content-type"] == "application/json"
    assert response_json(response)["message"]
    assert get_row(subscriptions_table) == before
    assert status_queue() == []


def test_a_second_cancel_returns_the_row_unchanged_and_sends_no_second_email(
    subscriptions_table, status_queue, ecpay, cancel
):
    active_row(subscriptions_table)
    first = cancel(AMY)
    assert [m["event_type"] for m in status_queue()] == ["cancel"]

    second = cancel(AMY)

    assert second["statusCode"] == 200
    assert response_json(second) == response_json(first)
    assert status_queue() == []
    assert len(ecpay.requests) == 1


def test_a_missing_row_is_404(subscriptions_table, status_queue, ecpay, cancel):
    active_row(subscriptions_table, route="TPE-SEL")

    response = cancel(AMY)

    assert response["statusCode"] == 404
    assert response_json(response)["message"]
    assert ecpay.requests == []


@pytest.mark.parametrize("status", ["pending_payment", "expired", None])
def test_a_row_that_is_not_active_is_409(subscriptions_table, status_queue, ecpay, cancel, status):
    before = active_row(subscriptions_table, subscription_status=status)

    response = cancel(AMY)

    assert response["statusCode"] == 409
    assert response_json(response)["message"]
    assert get_row(subscriptions_table) == before
    assert ecpay.requests == []
    assert status_queue() == []


@pytest.mark.parametrize("body", [
    "not json", [], {"route": "TPE-TYO"}, {"email": "amy@example.com"}, {"email": "", "route": "TPE-TYO"},
])
def test_a_malformed_request_is_400(subscriptions_table, ecpay, cancel, body):
    response = cancel(body)

    assert response["statusCode"] == 400
    assert ecpay.requests == []
