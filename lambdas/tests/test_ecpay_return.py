"""flight-ecpay-return: ECPay's server-to-server ReturnURL callback for the first charge.

Every callback here is signed by the tests' own CMV (conftest.ecpay_cmv) with the stage
keys, over an ECPay-shaped form body that includes the empty CustomField3/CustomField4.
"""

import base64
import re
import urllib.parse
from datetime import datetime, timezone

import pytest

from conftest import api_event, ecpay_cmv, load_lambda

TRADE_NO = "FP2610241200001A2B3C"


@pytest.fixture
def module(subscriptions_table, ecpay_secret):
    return load_lambda("flight-ecpay-return")


@pytest.fixture
def ecpay_return(module):
    return module.handler


def freeze_clock(monkeypatch, module, iso):
    """Pin the Lambda's clock (UTC) so period arithmetic can be checked against literals."""
    fixed = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)


def pending_row(table, **overrides):
    item = {
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 10000, "currency": "TWD",
        "created_at": "2026-10-24T03:00:00Z", "updated_at": "2026-10-24T03:00:00Z",
        "subscription_status": "pending_payment", "merchant_trade_no": TRADE_NO,
        "period_type": "M", "frequency": 1,
    }
    item.update(overrides)
    table.put_item(Item=item)
    return item


def get_row(table, email="amy@example.com", route="TPE-TYO"):
    return table.get_item(Key={"email": email, "route": route}).get("Item")


def callback_fields(**overrides):
    """The ReturnURL form for a paid first charge, unsigned. Pass a value of None to drop a key."""
    fields = {
        "MerchantID": "3002607",
        "MerchantTradeNo": TRADE_NO,
        "StoreID": "",
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "TradeNo": "2610241200123456",
        "TradeAmt": "300",
        "PaymentDate": "2026/10/24 12:00:30",
        "PaymentType": "Credit_CreditCard",
        "PaymentTypeChargeFee": "6",
        "TradeDate": "2026/10/24 12:00:00",
        "PlatformID": "",
        "SimulatePaid": "0",
        "Gwsr": "",
        "CustomField1": "amy@example.com",
        "CustomField2": "TPE-TYO",
        "CustomField3": "",
        "CustomField4": "",
    }
    fields.update(overrides)
    return {k: v for k, v in fields.items() if v is not None}


def signed(fields):
    return {**fields, "CheckMacValue": ecpay_cmv(fields)}


def post(handler, fields, base64_encoded=False):
    """POST the form the way ECPay does (application/x-www-form-urlencoded)."""
    body = urllib.parse.urlencode(fields)
    event = api_event("POST", "/ecpay-return", body)
    event["headers"] = {"content-type": "application/x-www-form-urlencoded"}
    if base64_encoded:
        event["body"] = base64.b64encode(body.encode()).decode()
        event["isBase64Encoded"] = True
    return handler(event, None)


def assert_ok(response):
    assert response["statusCode"] == 200
    assert response["headers"]["content-type"].startswith("text/plain")
    assert response["body"] == "1|OK"


# --------------------------------------------------------------- activation


def test_a_paid_first_charge_activates_the_row_and_replies_1_ok(ecpay_return, subscriptions_table):
    pending_row(subscriptions_table)

    response = post(ecpay_return, signed(callback_fields()))

    assert_ok(response)
    row = get_row(subscriptions_table)
    assert row["subscription_status"] == "active"
    assert row["merchant_trade_no"] == TRADE_NO
    assert row["total_success_times"] == 1
    for key in ("activated_at", "last_charged_at", "current_period_end"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row[key]), key
    assert row["current_period_end_date"] == row["current_period_end"][:10]
    # Untouched: the subscriber's own settings.
    assert row["target_price"] == 10000
    assert row["created_at"] == "2026-10-24T03:00:00Z"


@pytest.mark.parametrize(
    ("paid_at", "period_end"),
    [
        pytest.param("2026-10-24T04:00:30", "2026-11-24T04:00:30Z", id="next month, same day and time"),
        pytest.param("2026-01-31T16:00:00", "2026-02-28T16:00:00Z", id="31st clamps to end of February"),
        pytest.param("2028-01-30T00:00:00", "2028-02-29T00:00:00Z", id="leap year February"),
        pytest.param("2026-12-15T09:30:00", "2027-01-15T09:30:00Z", id="December rolls the year"),
    ],
)
def test_the_first_period_ends_one_calendar_month_after_the_charge(
    monkeypatch, module, ecpay_return, subscriptions_table, paid_at, period_end
):
    pending_row(subscriptions_table)
    freeze_clock(monkeypatch, module, paid_at)

    post(ecpay_return, signed(callback_fields()))

    row = get_row(subscriptions_table)
    assert row["current_period_end"] == period_end
    assert row["current_period_end_date"] == period_end[:10]
    assert row["activated_at"] == row["last_charged_at"] == f"{paid_at}Z"


def test_a_daily_period_ends_frequency_days_later(monkeypatch, module, ecpay_return, subscriptions_table):
    pending_row(subscriptions_table, period_type="D", frequency=2)
    freeze_clock(monkeypatch, module, "2026-10-31T23:00:00")

    post(ecpay_return, signed(callback_fields()))

    assert get_row(subscriptions_table)["current_period_end"] == "2026-11-02T23:00:00Z"


# --------------------------------------------------------------- verification


def assert_rejected(response, reason):
    assert response["statusCode"] == 400
    assert response["headers"]["content-type"].startswith("text/plain")
    assert response["body"] == f"0|{reason}"


def test_a_tampered_callback_is_rejected_and_writes_nothing(ecpay_return, subscriptions_table, capsys):
    before = pending_row(subscriptions_table)
    fields = signed(callback_fields())
    fields["TradeAmt"] = "1"  # changed after signing

    response = post(ecpay_return, fields)

    assert_rejected(response, "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before
    logged = capsys.readouterr().out
    assert fields["CheckMacValue"] in logged  # the MAC received
    assert ecpay_cmv(fields) in logged  # the MAC it should have had


def test_a_cmv_computed_without_the_empty_fields_is_rejected(ecpay_return, subscriptions_table):
    before = pending_row(subscriptions_table)
    fields = callback_fields()
    non_empty = {k: v for k, v in fields.items() if v != ""}

    response = post(ecpay_return, {**fields, "CheckMacValue": ecpay_cmv(non_empty)})

    assert_rejected(response, "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before


def test_a_lowercase_cmv_is_accepted(ecpay_return, subscriptions_table):
    pending_row(subscriptions_table)
    fields = callback_fields()

    assert_ok(post(ecpay_return, {**fields, "CheckMacValue": ecpay_cmv(fields).lower()}))
    assert get_row(subscriptions_table)["subscription_status"] == "active"


def test_a_missing_cmv_is_rejected(ecpay_return, subscriptions_table):
    before = pending_row(subscriptions_table)

    assert_rejected(post(ecpay_return, callback_fields()), "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before


def test_a_callback_for_another_merchant_is_rejected(ecpay_return, subscriptions_table):
    before = pending_row(subscriptions_table)

    response = post(ecpay_return, signed(callback_fields(MerchantID="2000132")))

    assert_rejected(response, "MerchantIDMismatch")
    assert get_row(subscriptions_table) == before


def test_a_base64_encoded_body_is_decoded(ecpay_return, subscriptions_table):
    pending_row(subscriptions_table)

    assert_ok(post(ecpay_return, signed(callback_fields()), base64_encoded=True))
    assert get_row(subscriptions_table)["subscription_status"] == "active"


# ------------------------------------------------------------ no-op callbacks


def test_a_simulated_payment_is_acknowledged_but_writes_nothing(ecpay_return, subscriptions_table):
    before = pending_row(subscriptions_table)

    response = post(ecpay_return, signed(callback_fields(SimulatePaid="1")))

    assert_ok(response)
    assert get_row(subscriptions_table) == before


@pytest.mark.parametrize("rtn_code", ["0", "10100058", "", "01"])
def test_a_failed_charge_leaves_the_row_pending(ecpay_return, subscriptions_table, capsys, rtn_code):
    before = pending_row(subscriptions_table)

    response = post(ecpay_return, signed(callback_fields(RtnCode=rtn_code, RtnMsg="授權失敗")))

    assert_ok(response)
    assert get_row(subscriptions_table) == before
    assert TRADE_NO in capsys.readouterr().out


def test_a_resent_callback_changes_nothing(monkeypatch, module, ecpay_return, subscriptions_table):
    pending_row(subscriptions_table)
    freeze_clock(monkeypatch, module, "2026-10-24T04:00:30")
    assert_ok(post(ecpay_return, signed(callback_fields())))
    activated = get_row(subscriptions_table)
    freeze_clock(monkeypatch, module, "2026-10-24T04:15:00")

    # ECPay's resends say RtnMsg=paid and may carry an empty Gwsr: idempotency ignores both.
    response = post(ecpay_return, signed(callback_fields(RtnMsg="paid")))

    assert_ok(response)
    assert get_row(subscriptions_table) == activated


def test_a_second_paid_checkout_does_not_replace_an_active_one(ecpay_return, subscriptions_table, capsys):
    pending_row(subscriptions_table)
    assert_ok(post(ecpay_return, signed(callback_fields())))
    activated = get_row(subscriptions_table)
    capsys.readouterr()

    response = post(ecpay_return, signed(callback_fields(MerchantTradeNo="FP2610241300009Z9Z9Z")))

    assert_ok(response)
    assert get_row(subscriptions_table) == activated
    logged = capsys.readouterr().out
    assert "warning" in logged.lower() and "FP2610241300009Z9Z9Z" in logged


def test_paying_an_older_checkout_attempt_still_activates_with_that_trade_number(ecpay_return, subscriptions_table):
    # The subscriber opened the cashier twice; the first attempt is the one that got paid.
    pending_row(subscriptions_table, merchant_trade_no="FP2610241300009Z9Z9Z")

    assert_ok(post(ecpay_return, signed(callback_fields())))

    row = get_row(subscriptions_table)
    assert row["subscription_status"] == "active"
    assert row["merchant_trade_no"] == TRADE_NO


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_a_late_resend_does_not_revive_a_row_that_has_moved_on(ecpay_return, subscriptions_table, status):
    before = pending_row(
        subscriptions_table, subscription_status=status, current_period_end="2026-11-24T04:00:30Z",
    )

    assert_ok(post(ecpay_return, signed(callback_fields(RtnMsg="paid"))))
    assert get_row(subscriptions_table) == before


def test_a_callback_with_no_matching_row_creates_nothing(ecpay_return, subscriptions_table, capsys):
    response = post(ecpay_return, signed(callback_fields(CustomField1="ghost@example.com")))

    assert_ok(response)
    assert subscriptions_table.scan()["Items"] == []
    assert "ghost@example.com" in capsys.readouterr().out


def test_a_callback_without_the_custom_fields_is_acknowledged_and_creates_nothing(ecpay_return, subscriptions_table):
    response = post(ecpay_return, signed(callback_fields(CustomField1="", CustomField2=None)))

    assert_ok(response)
    assert subscriptions_table.scan()["Items"] == []
