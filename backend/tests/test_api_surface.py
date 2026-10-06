"""The whole v1 surface exists and is honest.

Registering every route in Phase 0 means the OpenAPI schema -- and so the
generated frontend types -- is complete from the start. Nine of the sixteen
answer for real as of Phase 5; the rest still answer 501 with the phase that
fills them in, and none of them fabricates data. The two lists below are the
record of which is which, and they are what the suite checks rather than a
sentence in this docstring -- so this paragraph can go stale while the tests
cannot.

The 501 assertion splits here, once, structurally, rather than all sixteen
routes flipping at the end of the phase: each later task moves its own
routes from `DEFERRED_ROUTES` to `IMPLEMENTED_ROUTES` as it lands, which
keeps the per-task test gate alive instead of leaving the suite red for five
consecutive tasks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.inference import SchemaHashMismatch
from app.main import create_app

# (method, path, phase) triples: routes that still answer 501, with the exact
# phase string each one reports. `/metrics/drift` and `/models` stay deferred
# to Phase 7 (drift and active learning); `/ingest/start` to Phase 9 (real
# traffic); every other row here is this phase's to finish, and moves to
# IMPLEMENTED_ROUTES in the task that builds it.
DEFERRED_ROUTES: list[tuple[str, str, str]] = [
    ("GET", "/metrics/drift", "Phase 7 (drift and active learning)"),
    ("POST", "/ingest/start", "Phase 9 (real traffic)"),
    ("GET", "/models", "Phase 7 (drift and active learning)"),
]

# (method, path) pairs that must NOT answer 501.
IMPLEMENTED_ROUTES: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("POST", "/score"),
    ("GET", "/stream"),
    ("POST", "/replay/start"),
    ("POST", "/replay/stop"),
    ("GET", "/alerts"),
    ("GET", "/alerts/{alert_id}"),
    ("POST", "/alerts/{alert_id}/verdict"),
    ("GET", "/alerts/{alert_id}/related"),
    ("GET", "/metrics/model"),
    ("GET", "/metrics/threshold"),
    ("GET", "/analytics/summary"),
    ("GET", "/analytics/mitre-coverage"),
]

# The full documented surface, built from the two lists above rather than
# hand-restated, so `test_route_is_documented` still covers all sixteen pairs
# regardless of implementation status.
EXPECTED_ROUTES: list[tuple[str, str]] = [
    (method, path) for method, path, _phase in DEFERRED_ROUTES
] + IMPLEMENTED_ROUTES


def test_deferred_and_implemented_routes_partition_the_documented_surface() -> None:
    """The split's whole point, checked rather than trusted.

    Without this, a later task can move a route out of `DEFERRED_ROUTES` and
    forget to add it to `IMPLEMENTED_ROUTES`, and the route silently stops
    being checked by either half of this file.
    """
    deferred_pairs = [(method, path) for method, path, _phase in DEFERRED_ROUTES]
    implemented_pairs = list(IMPLEMENTED_ROUTES)

    assert len(deferred_pairs) + len(implemented_pairs) == 16
    assert set(deferred_pairs).isdisjoint(implemented_pairs)
    assert set(deferred_pairs) | set(implemented_pairs) == set(EXPECTED_ROUTES)


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


@pytest.mark.parametrize(("method", "path", "phase"), DEFERRED_ROUTES)
def test_unimplemented_routes_answer_501_with_a_phase(
    client: TestClient, api_prefix: str, method: str, path: str, phase: str
) -> None:
    concrete = path.replace("{alert_id}", "1")
    url = f"{api_prefix}{concrete}"
    params = {"t": 0.87} if concrete.endswith("/threshold") else None

    response = client.request(method, url, params=params)

    assert response.status_code == 501, response.text
    body = response.json()
    assert body["phase"] == phase
    assert body["endpoint"]


@pytest.mark.parametrize(("method", "path"), IMPLEMENTED_ROUTES)
def test_implemented_routes_do_not_answer_501(
    client: TestClient, api_prefix: str, method: str, path: str
) -> None:
    """The other half of the split, which is the half that can rot silently.

    Moving a route into `IMPLEMENTED_ROUTES` removes it from the 501 check, so
    without this nothing would notice a route regressing back to a stub -- the
    partition test would still pass, because the route is still accounted for
    in exactly one list. An empty body is sent where one is required; a 422
    from the validation layer is a real answer and not a stub, which is the
    distinction being asserted.
    """
    url = f"{api_prefix}{path}"

    response = client.request(method, url, json=[] if method == "POST" else None)

    assert response.status_code != 501, response.text


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
