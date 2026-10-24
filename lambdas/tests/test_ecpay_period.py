"""flight-ecpay-period: ECPay's server-to-server PeriodReturnURL callback for renewals.

From the second charge on, ECPay charges the card itself and POSTs each result here. Every
callback is signed by the tests' own CMV (conftest.ecpay_cmv) with the stage keys, over
the documented PeriodReturnURL field set, empty CustomField3/CustomField4 included.
"""

import base64
import urllib.parse
from datetime import datetime, timezone

import pytest

from conftest import api_event, ecpay_cmv, load_lambda

TRADE_NO = "FP2610241200001A2B3C"


@pytest.fixture
def module(subscriptions_table, ecpay_secret):
    return load_lambda("flight-ecpay-period")


@pytest.fixture
def ecpay_period(module):
    return module.handler


def freeze_clock(monkeypatch, module, iso):
    """Pin the Lambda's clock (UTC) so last_charged_at can be checked against a literal."""
    fixed = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)


def active_row(table, **overrides):
    """A subscriber whose first month was paid on 2026-10-24."""
    item = {
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 10000, "currency": "TWD",
        "created_at": "2026-10-24T03:00:00Z", "updated_at": "2026-10-24T03:00:00Z",
        "subscription_status": "active", "merchant_trade_no": TRADE_NO,
        "period_type": "M", "frequency": 1, "exec_times": 999,
        "activated_at": "2026-10-24T04:00:30Z", "last_charged_at": "2026-10-24T04:00:30Z",
        "total_success_times": 1,
        "current_period_end": "2026-11-24T04:00:30Z", "current_period_end_date": "2026-11-24",
    }
    item.update(overrides)
    table.put_item(Item=item)
    return item


def get_row(table, email="amy@example.com", route="TPE-TYO"):
    return table.get_item(Key={"email": email, "route": route}).get("Item")


def renewal_fields(**overrides):
    """The PeriodReturnURL form for the 2nd charge, unsigned. Pass a value of None to drop a key."""
    fields = {
        "MerchantID": "3002607",
        "MerchantTradeNo": TRADE_NO,
        "StoreID": "",
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "PeriodType": "M",
        "Frequency": "1",
        "ExecTimes": "999",
        "Amount": "300",
        "Gwsr": "12345678",
        "ProcessDate": "2026/11/24 12:00:30",
        "AuthCode": "777777",
        "FirstAuthAmount": "300",
        "TotalSuccessTimes": "2",
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
    event = api_event("POST", "/ecpay-period", body)
    event["headers"] = {"content-type": "application/x-www-form-urlencoded"}
    if base64_encoded:
        event["body"] = base64.b64encode(body.encode()).decode()
        event["isBase64Encoded"] = True
    return handler(event, None)


def assert_ok(response):
    assert response["statusCode"] == 200
    assert response["headers"]["content-type"].startswith("text/plain")
    assert response["body"] == "1|OK"


# ------------------------------------------------------------------ renewal


def test_a_paid_renewal_moves_the_paid_through_date_one_month_on(
    monkeypatch, module, ecpay_period, subscriptions_table
):
    active_row(subscriptions_table, failed_charge_count=2)
    freeze_clock(monkeypatch, module, "2026-11-24T04:01:00")

    response = post(ecpay_period, signed(renewal_fields()))

    assert_ok(response)
    row = get_row(subscriptions_table)
    assert row["subscription_status"] == "active"
    assert row["current_period_end"] == "2026-12-24T04:00:30Z"
    assert row["current_period_end_date"] == "2026-12-24"
    assert row["total_success_times"] == 2
    assert row["last_charged_at"] == "2026-11-24T04:01:00Z"
    assert row["failed_charge_count"] == 0
    # Untouched: when the subscription started, and the subscriber's own settings.
    assert row["activated_at"] == "2026-10-24T04:00:30Z"
    assert row["target_price"] == 10000


@pytest.mark.parametrize(
    ("previous_end", "next_end"),
    [
        pytest.param("2026-01-31T16:00:00Z", "2026-02-28T16:00:00Z", id="31st clamps to end of February"),
        pytest.param("2028-01-31T00:00:00Z", "2028-02-29T00:00:00Z", id="leap year February"),
        pytest.param("2026-03-31T09:30:00Z", "2026-04-30T09:30:00Z", id="31st clamps to the 30th"),
        pytest.param("2026-12-15T09:30:00Z", "2027-01-15T09:30:00Z", id="December rolls the year"),
    ],
)
def test_a_monthly_renewal_adds_one_calendar_month_clamped_to_month_end(
    ecpay_period, subscriptions_table, previous_end, next_end
):
    active_row(subscriptions_table, current_period_end=previous_end, current_period_end_date=previous_end[:10])

    assert_ok(post(ecpay_period, signed(renewal_fields())))

    row = get_row(subscriptions_table)
    assert row["current_period_end"] == next_end
    assert row["current_period_end_date"] == next_end[:10]


def test_a_daily_renewal_adds_frequency_days(ecpay_period, subscriptions_table):
    # The operator's renewal test runs on D / 1 / 2; any frequency works the same way.
    active_row(subscriptions_table, period_type="D", frequency=3, exec_times=10,
               current_period_end="2026-10-30T23:00:00Z", current_period_end_date="2026-10-30")

    assert_ok(post(ecpay_period, signed(renewal_fields(PeriodType="D", Frequency="3", ExecTimes="10"))))

    row = get_row(subscriptions_table)
    assert row["current_period_end"] == "2026-11-02T23:00:00Z"
    assert row["current_period_end_date"] == "2026-11-02"
    assert row["subscription_status"] == "active"


def test_a_late_charge_on_a_cancelled_row_moves_the_date_and_keeps_it_cancelled(ecpay_period, subscriptions_table):
    # ECPay charged once more before the Cancel took effect: the subscriber paid for it.
    active_row(subscriptions_table, subscription_status="cancelled", cancelled_at="2026-11-20T08:00:00Z")

    assert_ok(post(ecpay_period, signed(renewal_fields())))

    row = get_row(subscriptions_table)
    assert row["subscription_status"] == "cancelled"
    assert row["current_period_end"] == "2026-12-24T04:00:30Z"
    assert row["total_success_times"] == 2
    assert row["cancelled_at"] == "2026-11-20T08:00:00Z"


def test_the_last_charge_of_the_series_pays_its_period_then_lets_it_lapse(ecpay_period, subscriptions_table):
    # The operator's D / 1 / 2 test: the 2nd charge is the last one ECPay will make.
    active_row(subscriptions_table, period_type="D", frequency=1, exec_times=2,
               current_period_end="2026-10-25T04:00:30Z", current_period_end_date="2026-10-25")

    assert_ok(post(ecpay_period, signed(renewal_fields(PeriodType="D", ExecTimes="2", TotalSuccessTimes="2"))))

    row = get_row(subscriptions_table)
    assert row["subscription_status"] == "cancelled"  # the parser expires it once the period ends
    assert row["current_period_end"] == "2026-10-26T04:00:30Z"
    assert row["total_success_times"] == 2


def test_a_charge_before_the_last_one_keeps_the_series_active(ecpay_period, subscriptions_table):
    active_row(subscriptions_table, exec_times=3)

    assert_ok(post(ecpay_period, signed(renewal_fields(ExecTimes="3", TotalSuccessTimes="2"))))

    assert get_row(subscriptions_table)["subscription_status"] == "active"


def test_the_series_length_falls_back_to_the_row_when_the_payload_omits_it(ecpay_period, subscriptions_table):
    active_row(subscriptions_table, exec_times=2)

    assert_ok(post(ecpay_period, signed(renewal_fields(ExecTimes=None, TotalSuccessTimes="2"))))

    assert get_row(subscriptions_table)["subscription_status"] == "cancelled"


# --------------------------------------------------------------- failed renewals


def declined(**overrides):
    return renewal_fields(**{"RtnCode": "10100058", "RtnMsg": "授權失敗", "TotalSuccessTimes": "1",
                             "AuthCode": "", **overrides})


@pytest.mark.parametrize("rtn_code", ["0", "10100058", "", "01"])
def test_a_declined_renewal_counts_the_failure_and_keeps_the_subscriber(
    ecpay_period, subscriptions_table, capsys, rtn_code
):
    before = active_row(subscriptions_table)

    response = post(ecpay_period, signed(declined(RtnCode=rtn_code)))

    assert_ok(response)
    row = get_row(subscriptions_table)
    assert row["failed_charge_count"] == 1
    assert row["subscription_status"] == "active"
    for key in ("current_period_end", "current_period_end_date", "total_success_times", "last_charged_at"):
        assert row[key] == before[key], key
    assert TRADE_NO in capsys.readouterr().out


def test_failures_keep_counting_up(ecpay_period, subscriptions_table):
    active_row(subscriptions_table, failed_charge_count=3)

    assert_ok(post(ecpay_period, signed(declined())))

    row = get_row(subscriptions_table)
    assert (row["failed_charge_count"], row["subscription_status"]) == (4, "active")


@pytest.mark.parametrize("status", ["active", "cancelled"])
def test_the_sixth_failure_in_a_row_ends_the_subscription(ecpay_period, subscriptions_table, status):
    # ECPay terminates the series itself on the 6th consecutive failed charge.
    active_row(subscriptions_table, subscription_status=status, failed_charge_count=5)

    assert_ok(post(ecpay_period, signed(declined())))

    row = get_row(subscriptions_table)
    assert (row["failed_charge_count"], row["subscription_status"]) == (6, "expired")
    assert row["current_period_end"] == "2026-11-24T04:00:30Z"


def test_a_failure_that_shows_the_series_has_ended_expires_at_once(ecpay_period, subscriptions_table):
    # ExecStatus is how ECPay's period query reports a series: 0 means it has been terminated.
    active_row(subscriptions_table)

    assert_ok(post(ecpay_period, signed(declined(ExecStatus="0"))))

    assert get_row(subscriptions_table)["subscription_status"] == "expired"


def test_a_paid_renewal_after_failures_resets_the_count(ecpay_period, subscriptions_table):
    active_row(subscriptions_table, failed_charge_count=5)

    assert_ok(post(ecpay_period, signed(renewal_fields())))

    row = get_row(subscriptions_table)
    assert (row["failed_charge_count"], row["subscription_status"]) == (0, "active")


def test_a_failure_for_another_order_does_not_count(ecpay_period, subscriptions_table):
    before = active_row(subscriptions_table, merchant_trade_no="FP2612011200009Z9Z9Z", failed_charge_count=5)

    assert_ok(post(ecpay_period, signed(declined())))

    assert get_row(subscriptions_table) == before


# --------------------------------------------------------------- verification


def assert_rejected(response, reason):
    assert response["statusCode"] == 400
    assert response["headers"]["content-type"].startswith("text/plain")
    assert response["body"] == f"0|{reason}"


def test_a_tampered_callback_is_rejected_and_writes_nothing(ecpay_period, subscriptions_table, capsys):
    before = active_row(subscriptions_table)
    fields = signed(renewal_fields())
    fields["TotalSuccessTimes"] = "99"  # changed after signing

    response = post(ecpay_period, fields)

    assert_rejected(response, "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before
    logged = capsys.readouterr().out
    assert fields["CheckMacValue"] in logged  # the MAC received
    assert ecpay_cmv(fields) in logged  # the MAC it should have had


def test_a_cmv_computed_without_the_empty_fields_is_rejected(ecpay_period, subscriptions_table):
    before = active_row(subscriptions_table)
    fields = renewal_fields()
    non_empty = {k: v for k, v in fields.items() if v != ""}

    response = post(ecpay_period, {**fields, "CheckMacValue": ecpay_cmv(non_empty)})

    assert_rejected(response, "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before


def test_a_lowercase_cmv_is_accepted(ecpay_period, subscriptions_table):
    active_row(subscriptions_table)
    fields = renewal_fields()

    assert_ok(post(ecpay_period, {**fields, "CheckMacValue": ecpay_cmv(fields).lower()}))
    assert get_row(subscriptions_table)["total_success_times"] == 2


def test_a_missing_cmv_is_rejected(ecpay_period, subscriptions_table):
    before = active_row(subscriptions_table)

    assert_rejected(post(ecpay_period, renewal_fields()), "CheckMacValueInvalid")
    assert get_row(subscriptions_table) == before


def test_a_callback_for_another_merchant_is_rejected(ecpay_period, subscriptions_table):
    before = active_row(subscriptions_table)

    response = post(ecpay_period, signed(renewal_fields(MerchantID="2000132")))

    assert_rejected(response, "MerchantIDMismatch")
    assert get_row(subscriptions_table) == before


def test_a_base64_encoded_body_is_decoded(ecpay_period, subscriptions_table):
    active_row(subscriptions_table)

    assert_ok(post(ecpay_period, signed(renewal_fields()), base64_encoded=True))
    assert get_row(subscriptions_table)["total_success_times"] == 2


def test_the_verified_payload_is_logged_without_its_check_mac_value(ecpay_period, subscriptions_table, capsys):
    active_row(subscriptions_table)
    fields = signed(renewal_fields())

    post(ecpay_period, fields)

    logged = capsys.readouterr().out
    for name in ("TotalSuccessTimes", "ExecTimes", "ProcessDate", "AuthCode", "Gwsr", "CustomField4"):
        assert f'"{name}"' in logged, name
    assert "CheckMacValue" not in logged
    assert fields["CheckMacValue"] not in logged


# ------------------------------------------------------------ no-op callbacks


def test_a_simulated_payment_is_acknowledged_but_writes_nothing(ecpay_period, subscriptions_table):
    before = active_row(subscriptions_table)

    response = post(ecpay_period, signed(renewal_fields(SimulatePaid="1")))

    assert_ok(response)
    assert get_row(subscriptions_table) == before


def test_a_resent_renewal_does_not_move_the_date_twice(
    monkeypatch, module, ecpay_period, subscriptions_table
):
    active_row(subscriptions_table)
    freeze_clock(monkeypatch, module, "2026-11-24T04:01:00")
    assert_ok(post(ecpay_period, signed(renewal_fields())))
    renewed = get_row(subscriptions_table)
    freeze_clock(monkeypatch, module, "2026-11-24T04:15:00")

    # ECPay's resends say RtnMsg=paid; the charge count is what identifies the charge.
    response = post(ecpay_period, signed(renewal_fields(RtnMsg="paid")))

    assert_ok(response)
    assert get_row(subscriptions_table) == renewed


def test_a_renewal_for_another_order_does_not_touch_the_row(ecpay_period, subscriptions_table, capsys):
    # The row has moved on to a newer checkout; a renewal of the old series isn't its charge.
    before = active_row(subscriptions_table, merchant_trade_no="FP2612011200009Z9Z9Z")

    response = post(ecpay_period, signed(renewal_fields()))

    assert_ok(response)
    assert get_row(subscriptions_table) == before
    logged = capsys.readouterr().out
    assert "warning" in logged.lower() and "FP2612011200009Z9Z9Z" in logged


@pytest.mark.parametrize("status", ["pending_payment", "expired"])
def test_a_renewal_for_a_row_that_is_not_paid_up_is_logged_and_ignored(
    ecpay_period, subscriptions_table, capsys, status
):
    before = active_row(subscriptions_table, subscription_status=status)

    response = post(ecpay_period, signed(renewal_fields()))

    assert_ok(response)
    assert get_row(subscriptions_table) == before
    assert "warning" in capsys.readouterr().out.lower()


def test_a_renewal_with_no_matching_row_creates_nothing(ecpay_period, subscriptions_table, capsys):
    response = post(ecpay_period, signed(renewal_fields(CustomField1="ghost@example.com")))

    assert_ok(response)
    assert subscriptions_table.scan()["Items"] == []
    assert "ghost@example.com" in capsys.readouterr().out
