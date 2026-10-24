import json
import re

import pytest

from conftest import ECPAY_STAGE, api_event, ecpay_cmv, load_lambda, parse_checkout_form, response_json

API = "https://abc123.execute-api.us-east-1.amazonaws.com"
STAGE_CASHIER = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"


@pytest.fixture
def save(monkeypatch, subscriptions_table, ecpay_secret):
    # A stray newline/space in a URL changes the CMV; the Lambda must strip it.
    monkeypatch.setenv("API_BASE_URL", f"  {API}/\n")
    return load_lambda("flight-save-subscription").handler


def subscribe(save, body):
    return save(api_event("POST", "/subscribe", body), None)


def checkout(save, body):
    """POST /subscribe expecting the ECPay checkout page; returns the parsed form."""
    response = subscribe(save, body)
    assert response["statusCode"] == 200
    assert response["headers"]["content-type"].startswith("text/html")
    return parse_checkout_form(response["body"])


def get_row(table, email, route):
    return table.get_item(Key={"email": email, "route": route}).get("Item")


AMY_TOKYO = {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 10000}


# ------------------------------------------------------------ checkout form


def test_new_subscriber_gets_an_auto_submit_form_to_the_stage_cashier(save):
    form = checkout(save, AMY_TOKYO)

    assert form["action"] == STAGE_CASHIER
    assert form["method"] == "post"
    assert form["html"].rstrip().endswith("</html>")
    assert any("document.forms[0].submit()" in s for s in form["scripts"])
    # The inline submit script comes after the form, so it runs once document.write has it.
    assert form["html"].index("</form>") < form["html"].index("document.forms[0].submit()")


def test_form_is_a_monthly_recurring_credit_card_order_for_this_subscriber(save):
    fields = checkout(save, AMY_TOKYO)["fields"]

    assert fields["MerchantID"] == "3002607"
    assert fields["PaymentType"] == "aio"
    assert fields["ChoosePayment"] == "Credit"
    assert fields["EncryptType"] == "1"
    assert (fields["PeriodType"], fields["Frequency"], fields["ExecTimes"]) == ("M", "1", "999")
    assert fields["TotalAmount"] == fields["PeriodAmount"] == "300"
    assert fields["CustomField1"] == "amy@example.com"
    assert fields["CustomField2"] == "TPE-TYO"
    assert re.fullmatch(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", fields["MerchantTradeDate"])
    assert fields["ItemName"] and fields["TradeDesc"]
    for text in (fields["ItemName"], fields["TradeDesc"]):
        assert not re.search(r"curl|python|wget|script", text, re.IGNORECASE)


def test_callback_urls_point_at_the_api_and_are_stripped(save):
    fields = checkout(save, AMY_TOKYO)["fields"]

    assert fields["ReturnURL"] == f"{API}/ecpay-return"
    assert fields["PeriodReturnURL"] == f"{API}/ecpay-period"
    assert fields["OrderResultURL"] == f"{API}/ecpay-result"


def test_form_carries_a_valid_check_mac_value(save):
    fields = checkout(save, AMY_TOKYO)["fields"]

    assert fields["CheckMacValue"] == ecpay_cmv(fields)


def test_amount_is_read_from_the_secret(monkeypatch, subscriptions_table, ecpay_secret):
    ecpay_secret.put_secret_value(SecretId="flight/ecpay", SecretString=json.dumps({**ECPAY_STAGE, "amount": "450"}))
    monkeypatch.setenv("API_BASE_URL", API)
    save = load_lambda("flight-save-subscription").handler

    fields = checkout(save, AMY_TOKYO)["fields"]

    assert fields["TotalAmount"] == fields["PeriodAmount"] == "450"
    assert fields["CheckMacValue"] == ecpay_cmv(fields)


def test_form_values_are_html_escaped(save):
    form = checkout(save, {**AMY_TOKYO, "email": 'a"b<c>@example.com'})

    assert form["fields"]["CustomField1"] == 'a"b<c>@example.com'
    assert form["fields"]["CheckMacValue"] == ecpay_cmv(form["fields"])


def test_missing_ecpay_secret_fails_without_saving_a_row(monkeypatch, subscriptions_table):
    monkeypatch.setenv("API_BASE_URL", API)
    save = load_lambda("flight-save-subscription").handler

    with pytest.raises(Exception):
        subscribe(save, AMY_TOKYO)

    assert subscriptions_table.scan()["Items"] == []


# --------------------------------------------------------------- the row


def test_checkout_saves_a_pending_payment_row_with_the_trade_number(save, subscriptions_table):
    fields = checkout(save, AMY_TOKYO)["fields"]

    row = get_row(subscriptions_table, "amy@example.com", "TPE-TYO")
    assert row["subscription_status"] == "pending_payment"
    assert row["merchant_trade_no"] == fields["MerchantTradeNo"]
    assert (row["period_type"], row["frequency"]) == ("M", 1)
    assert row["plan_name"] == "tokyo"
    assert (row["origin"], row["destination"]) == ("TPE", "TYO")
    assert row["target_price"] == 10000
    assert row["currency"] == "TWD"
    assert row["created_at"] and row["updated_at"]


def test_trade_number_is_at_most_20_alphanumeric_characters(save):
    trade_no = checkout(save, AMY_TOKYO)["fields"]["MerchantTradeNo"]

    assert re.fullmatch(r"[A-Za-z0-9]{1,20}", trade_no)


def test_each_checkout_attempt_gets_a_new_trade_number(save, subscriptions_table):
    numbers = {checkout(save, AMY_TOKYO)["fields"]["MerchantTradeNo"] for _ in range(5)}

    assert len(numbers) == 5
    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["merchant_trade_no"] in numbers


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param({}, id="legacy row with no status"),
        pytest.param({"subscription_status": "pending_payment", "merchant_trade_no": "FPOLD1"}, id="pending_payment"),
        pytest.param({"subscription_status": "expired", "merchant_trade_no": "FPOLD1"}, id="expired"),
    ],
)
def test_unpaid_rows_go_back_through_checkout(save, subscriptions_table, existing):
    subscriptions_table.put_item(Item={
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 12000, "currency": "TWD",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", **existing,
    })

    fields = checkout(save, {**AMY_TOKYO, "target_price": 9000})["fields"]

    row = get_row(subscriptions_table, "amy@example.com", "TPE-TYO")
    assert row["subscription_status"] == "pending_payment"
    assert row["merchant_trade_no"] == fields["MerchantTradeNo"] != "FPOLD1"
    assert row["target_price"] == 9000
    assert row["created_at"] == "2026-01-01T00:00:00Z"
    assert row["updated_at"] > "2026-01-01T00:00:00Z"


ACTIVE = {
    "subscription_status": "active", "merchant_trade_no": "FP2610241200001A2B3C",
    "period_type": "M", "frequency": 1, "activated_at": "2026-01-01T00:00:30Z",
    "last_charged_at": "2026-01-01T00:00:30Z", "total_success_times": 1,
    "current_period_end": "2026-02-01T00:00:30Z", "current_period_end_date": "2026-02-01",
}


def test_an_active_subscriber_updates_the_target_in_place_without_paying_again(save, subscriptions_table):
    seeded = {
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 12000, "currency": "TWD",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", **ACTIVE,
    }
    subscriptions_table.put_item(Item=seeded)

    response = subscribe(save, {**AMY_TOKYO, "target_price": 9000})

    assert response["statusCode"] == 200
    assert response["headers"]["content-type"] == "application/json"
    row = get_row(subscriptions_table, "amy@example.com", "TPE-TYO")
    assert row["target_price"] == 9000
    assert row["updated_at"] > "2026-01-01T00:00:00Z"
    # Everything else, the payment state above all, is untouched.
    assert {k: v for k, v in row.items() if k not in ("target_price", "updated_at")} == {
        k: v for k, v in seeded.items() if k not in ("target_price", "updated_at")
    }
    assert response_json(response) == {**response_json(response), **{
        "email": "amy@example.com", "route": "TPE-TYO", "target_price": 9000,
        "subscription_status": "active", "current_period_end_date": "2026-02-01",
        "updated_at": row["updated_at"],
    }}


def test_saving_again_overwrites_the_target_instead_of_duplicating(save, subscriptions_table):
    checkout(save, {**AMY_TOKYO, "target_price": 12000})
    checkout(save, {**AMY_TOKYO, "target_price": 9000})

    rows = subscriptions_table.scan()["Items"]
    assert len(rows) == 1
    assert rows[0]["target_price"] == 9000


@pytest.mark.parametrize(
    ("plan_name", "route"),
    [("tokyo", "TPE-TYO"), ("seoul", "TPE-SEL"), ("london", "TPE-LON")],
)
def test_each_plan_checks_out_its_own_route(save, subscriptions_table, plan_name, route):
    fields = checkout(save, {**AMY_TOKYO, "plan_name": plan_name})["fields"]

    assert fields["CustomField2"] == route
    assert get_row(subscriptions_table, "amy@example.com", route)["plan_name"] == plan_name


def test_tokyo_and_seoul_are_independent_subscriptions(save, subscriptions_table):
    checkout(save, {**AMY_TOKYO, "target_price": 10000})
    checkout(save, {**AMY_TOKYO, "plan_name": "seoul", "target_price": 6000})

    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["target_price"] == 10000
    assert get_row(subscriptions_table, "amy@example.com", "TPE-SEL")["target_price"] == 6000


def test_accepts_a_numeric_string_target(save, subscriptions_table):
    checkout(save, {**AMY_TOKYO, "target_price": "8500"})

    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["target_price"] == 8500


# ------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**AMY_TOKYO, "plan_name": "osaka"}, id="unknown plan"),
        pytest.param({k: v for k, v in AMY_TOKYO.items() if k != "plan_name"}, id="missing plan"),
        pytest.param({k: v for k, v in AMY_TOKYO.items() if k != "target_price"}, id="missing target"),
        pytest.param({**AMY_TOKYO, "target_price": 0}, id="zero target"),
        pytest.param({**AMY_TOKYO, "target_price": -100}, id="negative target"),
        pytest.param({**AMY_TOKYO, "target_price": "cheap"}, id="non-numeric target"),
        pytest.param({**AMY_TOKYO, "target_price": "NaN"}, id="NaN target"),
        pytest.param({**AMY_TOKYO, "target_price": True}, id="boolean target"),
        pytest.param({**AMY_TOKYO, "target_price": None}, id="null target"),
        pytest.param({k: v for k, v in AMY_TOKYO.items() if k != "email"}, id="missing email"),
        pytest.param({**AMY_TOKYO, "email": "not-an-email"}, id="malformed email"),
        pytest.param("{not json", id="malformed body"),
        pytest.param("[1, 2]", id="non-object body"),
    ],
)
def test_rejects_bad_input_with_a_4xx_message_and_saves_nothing(save, subscriptions_table, body):
    response = subscribe(save, body)

    assert 400 <= response["statusCode"] < 500
    assert response["headers"]["content-type"] == "application/json"
    assert response_json(response)["message"]
    assert subscriptions_table.scan()["Items"] == []
