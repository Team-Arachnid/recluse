"""The Phase 0 checkpoint contract: GET /health."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_the_agreed_three_fields(client: TestClient, api_prefix: str) -> None:
    response = client.get(f"{api_prefix}/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "model_version", "uptime_s"}


def test_an_empty_artifacts_directory_reports_unloaded(tmp_path) -> None:
    """No trained model is an expected state, not a failure.

    A clean clone has no artifacts -- they are reproducible output and are
    gitignored -- so the service has to serve health and the dashboard shell
    without them.
    """
    from app.inference import load_bundle

    bundle = load_bundle(tmp_path)

    assert bundle.status == "ok"
    assert bundle.version == "unloaded"
    assert not bundle.stage1_ready


def test_health_reports_the_version_of_whatever_is_on_disk(
    client: TestClient, api_prefix: str
) -> None:
    """The version is read from the artifacts, never hardcoded.

    Which means this passes before Phase 2 has been run locally (`unloaded`)
    and after it (the promoted model card's version), and fails if the endpoint
    ever starts inventing one.
    """
    from app.config import settings
    from app.inference import load_bundle

    body = client.get(f"{api_prefix}/health").json()

    assert body["status"] == "ok"
    assert body["model_version"] == load_bundle(settings.artifacts_path).version


def test_uptime_is_non_negative_and_advances(client: TestClient, api_prefix: str) -> None:
    first = client.get(f"{api_prefix}/health").json()["uptime_s"]
    second = client.get(f"{api_prefix}/health").json()["uptime_s"]

    assert first >= 0
    assert second >= first


def test_health_is_mounted_under_the_configured_prefix(client: TestClient, api_prefix: str) -> None:
    """The prefix is configuration, so an unprefixed path must 404."""
    assert api_prefix == "/api/v1"
    assert client.get("/health").status_code == 404
