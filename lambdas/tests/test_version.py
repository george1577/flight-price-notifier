"""flight-version: GET /version tells the frontend which backend build is deployed."""

from conftest import api_event, load_lambda, response_json


def test_reports_the_deployed_backend_version_and_environment(monkeypatch):
    monkeypatch.setenv("BACKEND_VERSION", "3f2a9c1b0d4e.77aa01bc23de")
    monkeypatch.setenv("STACK_ENV", "dev")

    response = load_lambda("flight-version").handler(api_event("GET", "/version"), None)

    assert response["statusCode"] == 200
    assert response["headers"]["content-type"] == "application/json"
    assert response["headers"]["cache-control"] == "no-store"
    assert response_json(response) == {"backend_version": "3f2a9c1b0d4e.77aa01bc23de", "env": "dev"}


def test_an_unstamped_deploy_says_unknown(monkeypatch):
    monkeypatch.delenv("BACKEND_VERSION", raising=False)
    monkeypatch.delenv("STACK_ENV", raising=False)

    body = response_json(load_lambda("flight-version").handler(api_event("GET", "/version"), None))

    assert body == {"backend_version": "unknown", "env": "unknown"}
