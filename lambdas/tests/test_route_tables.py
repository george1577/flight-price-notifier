"""The route list is deliberately duplicated (see infra/README.md); keep the backend copies in step."""

import json
import re
from pathlib import Path

import pytest

from conftest import load_lambda

REPO = Path(__file__).resolve().parents[2]


def committed_routes():
    return json.loads((REPO / "infra" / "flight-routes.json").read_text())


def test_scheduled_routes_match_the_plans_post_subscribe_accepts(aws):
    save = load_lambda("flight-save-subscription")

    scheduled = {r["plan"]: {"origin": r["origin"], "destination": r["destination"]} for r in committed_routes()}

    assert scheduled == save.PLANS


@pytest.mark.parametrize("function_name", ["flight-fare-notification", "flight-status-notification"])
def test_every_scheduled_route_has_city_names_for_the_email(aws, function_name):
    notifier = load_lambda(function_name)

    for r in committed_routes():
        assert r["origin"] in notifier.CITY_NAMES
        assert r["destination"] in notifier.CITY_NAMES


def test_dashboard_cards_cover_every_scheduled_route():
    plans_ts = (REPO / "src" / "lib" / "plans.ts").read_text()
    card_routes = set(re.findall(r'route: "([A-Z]{3}-[A-Z]{3})"', plans_ts))

    assert card_routes == {f'{r["origin"]}-{r["destination"]}' for r in committed_routes()}
