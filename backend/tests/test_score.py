"""POST /api/v1/score.

The route is a thin, stateless contract over `ModelBundle.score_batch`
(already tested against real artifacts in `test_fusion.py`): map flows in,
map decisions out, map the two failure modes `score_batch` can raise onto
the documented status codes. These tests pin that mapping and the schema's
own refusal of a string-valued feature, not the model's scoring behaviour.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.inference import ModelBundle, load_bundle
from app.main import create_app


def _client_for(bundle: ModelBundle) -> TestClient:
    """A TestClient wired to a specific bundle, bypassing the real lifespan.

    The real lifespan loads `app.state.bundle` from `settings.artifacts_path`,
    which is either empty (a clean checkout) or whatever happens to be on the
    developer's disk -- neither is something a test can pin a feature matrix
    against. Building the app and setting the same `app.state` attributes
    the lifespan would set exercises the real handler against a bundle this
    test controls, without mutating the session-scoped `client` fixture every
    other test in the suite shares.
    """
    app = create_app()
    app.state.bundle = bundle
    app.state.started_at = time.monotonic()
    return TestClient(app)


@pytest.fixture
def loaded_bundle(two_stage_artifacts) -> ModelBundle:
    bundle = load_bundle(two_stage_artifacts)
    assert bundle.stage1_ready and bundle.stage2_ready
    return bundle


def _flows(frame: pd.DataFrame, n: int) -> list[dict[str, float]]:
    """The first `n` rows of `frame` as plain feature dicts -- the request body shape.

    `label` is Phase 2's target column, not a flow feature. A real caller
    never sends it, and it is dropped here rather than sent, because
    `FlowRecord`'s open mapping would otherwise reject the batch for the
    string value under that key -- correctly, but not what this helper is
    for.
    """
    return frame.drop(columns="label").head(n).to_dict("records")


def test_a_batch_scores_one_result_per_row_in_order(
    loaded_bundle: ModelBundle, phase2_test: pd.DataFrame, api_prefix: str
) -> None:
    flows = _flows(phase2_test, 30)

    response = _client_for(loaded_bundle).post(f"{api_prefix}/score", json=flows)

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    expected = loaded_bundle.score_batch(flows)

    assert len(results) == len(flows)
    assert [r["kind"] for r in results] == [e["kind"] for e in expected]
    assert [r["family"] for r in results] == [e["family"] for e in expected]
    assert [r["detection_stage"] for r in results] == [e["detection_stage"] for e in expected]
    assert {r["model_version"] for r in results} == {loaded_bundle.version}


def test_a_string_valued_feature_is_refused_with_422_naming_the_field(
    client: TestClient, api_prefix: str
) -> None:
    """The `_verify_payload` failure mode, caught one layer up by the schema.

    Uses the shared session-scoped `client`: this is a request-body
    validation failure, so it is rejected before the handler ever reads
    `app.state.bundle`.
    """
    response = client.post(f"{api_prefix}/score", json=[{"flow_duration": "7.0"}])

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any("flow_duration" in str(error.get("loc", "")) for error in errors)


def test_an_empty_batch_returns_200_with_no_results(
    loaded_bundle: ModelBundle, api_prefix: str
) -> None:
    response = _client_for(loaded_bundle).post(f"{api_prefix}/score", json=[])

    assert response.status_code == 200, response.text
    assert response.json() == {"results": [], "scored": 0, "alerts": 0}


def test_scored_and_alerts_reflect_the_batch_rather_than_a_pinned_count(
    loaded_bundle: ModelBundle, phase2_test: pd.DataFrame, api_prefix: str
) -> None:
    """The relationship is asserted, not a hardcoded count, so this does not
    pin itself to one model's particular behaviour."""
    flows = _flows(phase2_test, 60)

    body = _client_for(loaded_bundle).post(f"{api_prefix}/score", json=flows).json()

    assert body["scored"] == len(flows)
    assert body["alerts"] == sum(1 for result in body["results"] if result["kind"] is not None)


def test_a_flow_that_raises_no_alert_serialises_with_everything_null(
    loaded_bundle: ModelBundle, phase2_test: pd.DataFrame, api_prefix: str
) -> None:
    benign = phase2_test[phase2_test["label"] == "BENIGN"]
    flows = _flows(benign, min(len(benign), 100))

    body = _client_for(loaded_bundle).post(f"{api_prefix}/score", json=flows).json()

    clear = next((result for result in body["results"] if result["kind"] is None), None)
    assert clear is not None, "expected at least one benign flow to raise no alert"
    assert clear["family"] is None
    assert clear["detection_stage"] is None


def test_scoring_without_a_loaded_stage_is_503_not_500(tmp_path, api_prefix: str) -> None:
    """A bundle with no complete stage is refused as such -- the same refusal
    `test_fusion.py` covers for the direct `score_batch` call -- and the
    route must map it to 503 rather than let it surface as an unhandled 500.

    Built from an empty `tmp_path`, the way a bad bundle is built elsewhere
    in this suite, rather than by mutating the session-scoped `client`.
    """
    empty = load_bundle(tmp_path)

    response = _client_for(empty).post(f"{api_prefix}/score", json=[{"destination_port": 443.0}])

    assert response.status_code == 503, response.text
