import base64
import urllib.parse

import pytest

from conftest import api_event, load_lambda

SITE = "https://flight.example.com"


@pytest.fixture
def result(monkeypatch, subscriptions_table):
    monkeypatch.setenv("SITE_URL", f" {SITE}/\n")
    return load_lambda("flight-ecpay-result").handler


def browser_post(fields, base64_encoded=False):
    """ECPay's OrderResultURL: the browser POSTs the payment result as a form."""
    body = urllib.parse.urlencode(fields)
    event = api_event("POST", "/ecpay-result", body)
    event["headers"] = {"content-type": "application/x-www-form-urlencoded"}
    if base64_encoded:
        event["body"] = base64.b64encode(body.encode()).decode()
        event["isBase64Encoded"] = True
    return event


PAID = {
    "MerchantID": "3002607", "MerchantTradeNo": "FP2610241200001A2B3C", "RtnCode": "1",
    "RtnMsg": "Succeeded", "CustomField1": "amy@example.com", "CustomField2": "TPE-TYO",
    "CustomField3": "", "CustomField4": "", "CheckMacValue": "ABC",
}


def test_paid_result_redirects_to_the_dashboard_with_success(result):
    response = result(browser_post(PAID), None)

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == f"{SITE}/app?purchase=success"


@pytest.mark.parametrize("rtn_code", ["0", "10100058", ""])
def test_failed_result_redirects_with_failed(result, rtn_code):
    response = result(browser_post({**PAID, "RtnCode": rtn_code}), None)

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == f"{SITE}/app?purchase=failed"


def test_base64_encoded_body_is_decoded(result):
    response = result(browser_post(PAID, base64_encoded=True), None)

    assert response["headers"]["location"] == f"{SITE}/app?purchase=success"


@pytest.mark.parametrize(
    ("query", "outcome"),
    [({"RtnCode": "1"}, "success"), ({"RtnCode": "0"}, "failed"), (None, "failed")],
)
def test_get_also_redirects(result, query, outcome):
    response = result(api_event("GET", "/ecpay-result", query=query), None)

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == f"{SITE}/app?purchase={outcome}"


def test_changes_no_subscription_state(result, subscriptions_table):
    subscriptions_table.put_item(Item={
        "email": "amy@example.com", "route": "TPE-TYO", "subscription_status": "pending_payment",
        "merchant_trade_no": "FP2610241200001A2B3C",
    })

    result(browser_post(PAID), None)

    rows = subscriptions_table.scan()["Items"]
    assert rows == [{
        "email": "amy@example.com", "route": "TPE-TYO", "subscription_status": "pending_payment",
        "merchant_trade_no": "FP2610241200001A2B3C",
    }]
