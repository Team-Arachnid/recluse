"""Phase 8 -- train/serve feature parity, on the shipped models and real flows.

`training/features.py` is the only place the feature matrix is built, and both
paths import it. That alone does not make the two paths agree: the serving path
receives JSON, which has no integers distinct from floats and no column order,
and the request schema coerces every value to a float. A destination port that
arrives as 443.0 instead of 443, or a column that arrives sixth instead of
second, is exactly the train/serve skew that scores a different flow than the
one that arrived and raises nothing.

So this builds the matrix twice from the committed demo flows -- real
CICIDS2017 rows, every label present -- once the way training does (a Parquet
frame straight into `build_feature_matrix`) and once the way serving does
(through a JSON round trip and `POST /score`), and requires the results to be
identical: the matrix bit for bit, and both stages' scores to the last digit
the wire carries.

`test_fusion.py` makes the same assertion against models trained in-test on
synthetic rows; this one makes it against the release a deployment serves.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.inference import ModelBundle, load_bundle
from app.main import create_app
from app.release import DEMO_FLOWS_FILE
from training.autoencoder import reconstruction_error
from training.features import LABEL_COLUMN, build_feature_matrix
from training.metrics import attack_confidence


@pytest.fixture(scope="module")
def bundle() -> ModelBundle:
    loaded = load_bundle(settings.artifacts_path)
    assert loaded.stage1_ready and loaded.stage2_ready
    return loaded


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    """Forty flows of every label in the demo sample, in capture order."""
    demo = pd.read_parquet(settings.release_path / DEMO_FLOWS_FILE)
    return demo.groupby(LABEL_COLUMN, sort=False).head(40).sort_index().reset_index(drop=True)


def _over_the_wire(frame: pd.DataFrame) -> list[dict[str, float]]:
    """The request body a client would send: JSON, round-tripped exactly.

    `json`, not `DataFrame.to_json`, whose default ten-digit precision would
    make the comparison fail for a reason that has nothing to do with the code.
    """
    return json.loads(json.dumps(frame.drop(columns=LABEL_COLUMN).to_dict("records")))


def test_the_serving_matrix_is_the_training_matrix(bundle: ModelBundle, frame) -> None:
    offline = build_feature_matrix(frame, bundle.preprocessing)
    served = build_feature_matrix(pd.DataFrame(_over_the_wire(frame)), bundle.preprocessing)

    assert list(served.columns) == list(offline.columns) == bundle.feature_order
    np.testing.assert_array_equal(
        served.to_numpy(dtype="float32"), offline.to_numpy(dtype="float32")
    )


def test_a_reordered_payload_builds_the_same_matrix(bundle: ModelBundle, frame) -> None:
    """JSON objects have no column order; the matrix builder has to impose one."""
    flows = _over_the_wire(frame)
    shuffled = [dict(reversed(list(flow.items()))) for flow in flows]

    np.testing.assert_array_equal(
        build_feature_matrix(pd.DataFrame(shuffled), bundle.preprocessing).to_numpy("float32"),
        build_feature_matrix(pd.DataFrame(flows), bundle.preprocessing).to_numpy("float32"),
    )


def test_score_returns_the_offline_scores_for_real_flows(
    bundle: ModelBundle, frame, api_prefix: str
) -> None:
    matrix = build_feature_matrix(frame, bundle.preprocessing).to_numpy(dtype="float32")
    confidence = attack_confidence(
        bundle.supervised.predict_proba(matrix), bundle.supervised_classes
    )
    errors = reconstruction_error(bundle.autoencoder, matrix)

    app = create_app()
    app.state.bundle = bundle
    app.state.started_at = time.monotonic()
    response = TestClient(app).post(f"{api_prefix}/score", json=_over_the_wire(frame))

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert len(results) == len(frame)
    assert [r["confidence"] for r in results] == pytest.approx(confidence.tolist(), rel=1e-12)

    # Stage 2 is consulted only where Stage 1 did not alert -- the cascade -- and
    # its score is null elsewhere. Where it was consulted it must be the offline
    # reconstruction error of the same row.
    consulted = [i for i, r in enumerate(results) if r["kind"] != "KNOWN"]
    assert [i for i, r in enumerate(results) if r["anomaly_score"] is not None] == consulted
    assert [results[i]["anomaly_score"] for i in consulted] == pytest.approx(
        errors[consulted].tolist(), rel=1e-12
    )
    # Both stages fired somewhere in a mix of every label, so the comparison
    # covered the fusion of both scores rather than one stage's silence.
    assert {r["kind"] for r in results} >= {"KNOWN", "UNCLASSIFIED_ANOMALY"}
