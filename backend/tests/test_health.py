"""The Phase 0 checkpoint contract: GET /health."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_the_agreed_three_fields(client: TestClient, api_prefix: str) -> None:
    response = client.get(f"{api_prefix}/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "model_version", "uptime_s"}


def test_health_status_is_ok_without_artifacts(client: TestClient, api_prefix: str) -> None:
    """No trained model yet is the expected Phase 0 state, not a failure."""
    body = client.get(f"{api_prefix}/health").json()

    assert body["status"] == "ok"
    assert body["model_version"] == "unloaded"


def test_uptime_is_non_negative_and_advances(client: TestClient, api_prefix: str) -> None:
    first = client.get(f"{api_prefix}/health").json()["uptime_s"]
    second = client.get(f"{api_prefix}/health").json()["uptime_s"]

    assert first >= 0
    assert second >= first


def test_health_is_mounted_under_the_configured_prefix(client: TestClient, api_prefix: str) -> None:
    """The prefix is configuration, so an unprefixed path must 404."""
    assert api_prefix == "/api/v1"
    assert client.get("/health").status_code == 404
