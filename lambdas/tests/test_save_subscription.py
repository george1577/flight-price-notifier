import pytest

from conftest import api_event, load_lambda, response_json


@pytest.fixture
def save(subscriptions_table):
    return load_lambda("flight-save-subscription").handler


def subscribe(save, body):
    return save(api_event("POST", "/subscribe", body), None)


def get_row(table, email, route):
    return table.get_item(Key={"email": email, "route": route}).get("Item")


def test_saves_a_tokyo_subscription(save, subscriptions_table):
    response = subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 10000})

    assert response["statusCode"] == 200
    row = get_row(subscriptions_table, "amy@example.com", "TPE-TYO")
    assert row["plan_name"] == "tokyo"
    assert row["origin"] == "TPE"
    assert row["destination"] == "TYO"
    assert row["target_price"] == 10000
    assert row["currency"] == "TWD"
    assert row["created_at"] and row["updated_at"]


def test_returns_the_saved_subscription_as_plain_json_numbers(save):
    response = subscribe(save, {"email": "amy@example.com", "plan_name": "seoul", "target_price": 5500})

    saved = response_json(response)
    assert saved["email"] == "amy@example.com"
    assert saved["route"] == "TPE-SEL"
    assert saved["plan_name"] == "seoul"
    assert saved["destination"] == "SEL"
    assert saved["target_price"] == 5500
    assert isinstance(saved["target_price"], int)
    assert saved["currency"] == "TWD"


def test_london_plan_saves_the_tpe_lon_route(save, subscriptions_table):
    response = subscribe(save, {"email": "amy@example.com", "plan_name": "london", "target_price": 28000})

    assert response["statusCode"] == 200
    row = get_row(subscriptions_table, "amy@example.com", "TPE-LON")
    assert (row["origin"], row["destination"], row["plan_name"]) == ("TPE", "LON", "london")


def test_saving_again_overwrites_the_target_instead_of_duplicating(save, subscriptions_table):
    first = response_json(
        subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 12000})
    )
    second = response_json(
        subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 9000})
    )

    rows = subscriptions_table.scan()["Items"]
    assert len(rows) == 1
    assert rows[0]["target_price"] == 9000
    assert second["target_price"] == 9000
    assert second["created_at"] == first["created_at"]


def test_updating_keeps_the_original_created_at(save, subscriptions_table):
    subscriptions_table.put_item(Item={
        "email": "amy@example.com", "route": "TPE-TYO", "plan_name": "tokyo",
        "origin": "TPE", "destination": "TYO", "target_price": 12000, "currency": "TWD",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    })

    saved = response_json(
        subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 9000})
    )

    assert saved["created_at"] == "2026-01-01T00:00:00Z"
    assert saved["updated_at"] > "2026-01-01T00:00:00Z"
    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["created_at"] == "2026-01-01T00:00:00Z"


def test_tokyo_and_seoul_are_independent_subscriptions(save, subscriptions_table):
    subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 10000})
    subscribe(save, {"email": "amy@example.com", "plan_name": "seoul", "target_price": 6000})

    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["target_price"] == 10000
    assert get_row(subscriptions_table, "amy@example.com", "TPE-SEL")["target_price"] == 6000


def test_accepts_a_numeric_string_target(save, subscriptions_table):
    response = subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": "8500"})

    assert response["statusCode"] == 200
    assert get_row(subscriptions_table, "amy@example.com", "TPE-TYO")["target_price"] == 8500


VALID = {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 10000}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**VALID, "plan_name": "osaka"}, id="unknown plan"),
        pytest.param({k: v for k, v in VALID.items() if k != "plan_name"}, id="missing plan"),
        pytest.param({k: v for k, v in VALID.items() if k != "target_price"}, id="missing target"),
        pytest.param({**VALID, "target_price": 0}, id="zero target"),
        pytest.param({**VALID, "target_price": -100}, id="negative target"),
        pytest.param({**VALID, "target_price": "cheap"}, id="non-numeric target"),
        pytest.param({**VALID, "target_price": "NaN"}, id="NaN target"),
        pytest.param({**VALID, "target_price": True}, id="boolean target"),
        pytest.param({**VALID, "target_price": None}, id="null target"),
        pytest.param({k: v for k, v in VALID.items() if k != "email"}, id="missing email"),
        pytest.param({**VALID, "email": "not-an-email"}, id="malformed email"),
        pytest.param("{not json", id="malformed body"),
        pytest.param("[1, 2]", id="non-object body"),
    ],
)
def test_rejects_bad_input_with_a_4xx_message_and_saves_nothing(save, subscriptions_table, body):
    response = subscribe(save, body)

    assert 400 <= response["statusCode"] < 500
    assert response_json(response)["message"]
    assert subscriptions_table.scan()["Items"] == []


def test_has_no_payment_fields(save, subscriptions_table):
    subscribe(save, {"email": "amy@example.com", "plan_name": "tokyo", "target_price": 10000})

    row = get_row(subscriptions_table, "amy@example.com", "TPE-TYO")
    assert set(row) == {
        "email", "route", "plan_name", "origin", "destination",
        "target_price", "currency", "created_at", "updated_at",
    }
