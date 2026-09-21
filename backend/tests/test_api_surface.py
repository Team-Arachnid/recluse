"""The v1 surface from BUILD_PROMPT.md Part 8 exists and is honest.

Registering every route in Phase 0 means the OpenAPI schema -- and so the
generated frontend types -- is complete from the start. Unimplemented routes
answer 501 with the phase that fills them in; none of them fabricates data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.inference import SchemaHashMismatch
from app.main import create_app

# (method, path) pairs exactly as Part 8 specifies them.
EXPECTED_ROUTES = [
    ("GET", "/health"),
    ("POST", "/score"),
    ("GET", "/alerts"),
    ("GET", "/alerts/{alert_id}"),
    ("POST", "/alerts/{alert_id}/verdict"),
    ("GET", "/alerts/{alert_id}/related"),
    ("GET", "/stream"),
    ("GET", "/metrics/model"),
    ("GET", "/metrics/threshold"),
    ("GET", "/metrics/drift"),
    ("GET", "/analytics/summary"),
    ("GET", "/analytics/mitre-coverage"),
    ("POST", "/replay/start"),
    ("POST", "/replay/stop"),
    ("POST", "/ingest/start"),
    ("GET", "/models"),
]


def _documented_operations(app) -> set[tuple[str, str]]:
    """Method/path pairs from the OpenAPI schema.

    Read from the schema rather than walking `app.routes`, because that is
    the artefact the frontend generates its types from -- a route missing
    here is a route the client cannot see.
    """
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_route_is_documented(method: str, path: str, api_prefix: str) -> None:
    assert (method, f"{api_prefix}{path}") in _documented_operations(create_app())


@pytest.mark.parametrize(
    ("method", "path"),
    [(method, path) for method, path in EXPECTED_ROUTES if path != "/health"],
)
def test_unimplemented_routes_answer_501_with_a_phase(
    client: TestClient, api_prefix: str, method: str, path: str
) -> None:
    concrete = path.replace("{alert_id}", "1")
    url = f"{api_prefix}{concrete}"
    params = {"t": 0.87} if concrete.endswith("/threshold") else None

    response = client.request(method, url, params=params)

    assert response.status_code == 501, response.text
    body = response.json()
    assert body["phase"].startswith("Phase ")
    assert body["endpoint"]


def test_openapi_schema_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["openapi"].startswith("3.")
    assert f"{schema['info']['title']}" == "Recluse API"


def test_no_route_mentions_blocking(client: TestClient) -> None:
    """There is no containment endpoint. The brief forbids auto-block, and the
    absence is asserted rather than assumed."""
    schema = client.get("/openapi.json").json()

    for path in schema["paths"]:
        assert "block" not in path.lower()
        assert "drop" not in path.lower()
        assert "quarantine" not in path.lower()


def test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash(tmp_path) -> None:
    """Train/serve skew is silent, so a bad bundle must stop the process.

    Written here in Phase 0 so the guard exists before there is anything to
    load -- the failure it prevents produces no exception on its own.
    """
    import pickle

    bundle_path = tmp_path / "preprocessing.pkl"
    with bundle_path.open("wb") as handle:
        pickle.dump(
            {
                "scaler": None,
                "feature_order": ["a", "b"],
                "dropped_columns": [],
                "port_encoding": {},
                "schema_hash": "sha256:deadbeef",  # does not match ["a", "b"]
            },
            handle,
        )

    from app.inference import load_bundle

    with pytest.raises(SchemaHashMismatch):
        load_bundle(tmp_path)
