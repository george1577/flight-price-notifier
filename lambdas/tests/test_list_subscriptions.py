from decimal import Decimal

import pytest

from conftest import api_event, load_lambda, response_json


@pytest.fixture
def list_subs(subscriptions_table):
    return load_lambda("flight-list-subscriptions").handler


def row(email, plan_name, route, target_price):
    return {
        "email": email, "route": route, "plan_name": plan_name,
        "origin": "TPE", "destination": route[4:], "target_price": target_price,
        "currency": "TWD", "created_at": "2026-10-23T00:00:00Z", "updated_at": "2026-10-23T00:00:00Z",
    }


def list_for(list_subs, query):
    return list_subs(api_event("GET", "/subscriptions", query=query), None)


def test_returns_only_the_requested_users_rows(list_subs, subscriptions_table):
    subscriptions_table.put_item(Item=row("amy@example.com", "tokyo", "TPE-TYO", 10000))
    subscriptions_table.put_item(Item=row("amy@example.com", "seoul", "TPE-SEL", 6000))
    subscriptions_table.put_item(Item=row("bob@example.com", "tokyo", "TPE-TYO", 8000))

    response = list_for(list_subs, {"email": "amy@example.com"})

    assert response["statusCode"] == 200
    rows = sorted(response_json(response), key=lambda r: r["route"])
    assert [(r["email"], r["route"], r["target_price"]) for r in rows] == [
        ("amy@example.com", "TPE-SEL", 6000),
        ("amy@example.com", "TPE-TYO", 10000),
    ]


def test_target_prices_are_plain_json_numbers(list_subs, subscriptions_table):
    subscriptions_table.put_item(Item=row("amy@example.com", "tokyo", "TPE-TYO", 10000))
    subscriptions_table.put_item(Item=row("amy@example.com", "seoul", "TPE-SEL", Decimal("5999.5")))

    response = list_for(list_subs, {"email": "amy@example.com"})

    assert '"target_price": 10000,' in response["body"]
    by_route = {r["route"]: r["target_price"] for r in response_json(response)}
    assert by_route == {"TPE-TYO": 10000, "TPE-SEL": 5999.5}
    assert isinstance(by_route["TPE-TYO"], int)


def test_a_user_with_no_subscriptions_gets_an_empty_list(list_subs):
    response = list_for(list_subs, {"email": "new@example.com"})

    assert response["statusCode"] == 200
    assert response_json(response) == []


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(None, id="no query string"),
        pytest.param({}, id="no email"),
        pytest.param({"email": ""}, id="empty email"),
        pytest.param({"email": "not-an-email"}, id="malformed email"),
    ],
)
def test_rejects_a_missing_or_malformed_email(list_subs, query):
    response = list_for(list_subs, query)

    assert response["statusCode"] == 400
    assert response_json(response)["message"]
