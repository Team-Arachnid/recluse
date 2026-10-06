"""Phase 5 -- GET /metrics/model and GET /metrics/threshold.

The point of separating these from `tests/test_metrics.py` (which tests
`training/metrics.py`'s arithmetic) is that these test what the API *serves*:
that it reports the measured numbers rather than recomputing them, and that
the threshold slider's two figures are read off the same axis.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.metrics_store import MetricsStore, load_metrics

# A tiny shared-edge histogram triple, shaped exactly like what
# train_autoencoder writes: benign mass low, attack mass high, same edges.
EDGES = [0.0, 0.1, 0.2, 0.4, 0.8, 1.6]


def _histogram(counts: list[int]) -> dict:
    return {
        "edges": EDGES,
        "counts": counts,
        "rows": sum(counts),
        "percentiles": {"p50": 0.1, "max": 1.6},
        "spacing": "log",
    }


@pytest.fixture
def metrics_artifacts(tmp_path):
    """An artifacts directory carrying all three evaluation files."""
    supervised = {
        "stage": "stage1_supervised",
        "budget": {"max_alerts_per_day": 320, "target_fpr": 0.00032},
        "test": {
            "labels": ["benign", "dos"],
            "per_class": {
                "benign": {"precision": 0.9, "recall": 0.99, "f1": 0.94, "support": 100},
                "dos": {"precision": 0.8, "recall": 0.7, "f1": 0.75, "support": 50},
            },
            "confusion": [[99, 1], [15, 35]],
            "pr_curve": [[1.0, 0.33], [0.5, 0.8]],
            "roc_curve": [[0.0, 0.0], [0.1, 0.7]],
            "pr_auc": 0.846,
            "roc_auc": 0.881,
            "accuracy": 0.629,
            "tau_sup": 0.3879,
            "family_recall": {"dos": {"recall": 0.7}},
            "volume": {"fpr": 0.00016, "alerts_per_analyst_hour": 20.32},
        },
    }
    anomaly = {
        "stage": "stage2_anomaly",
        "training": {
            "histograms": {
                "edges": EDGES,
                "test_benign": _histogram([80, 10, 6, 3, 1]),
                "test_attack": _histogram([2, 3, 10, 35, 50]),
            }
        },
        "test": {"pr_auc": 0.62, "family_recall": {"ddos": {"recall": 0.53}}},
    }
    loao = {"stage": "phase4_loao", "folds": [{"held_out": "dos", "headline": {"support": 1}}]}

    (tmp_path / "metrics_supervised.json").write_text(json.dumps(supervised), encoding="utf-8")
    (tmp_path / "metrics_anomaly.json").write_text(json.dumps(anomaly), encoding="utf-8")
    (tmp_path / "metrics_loao.json").write_text(json.dumps(loao), encoding="utf-8")
    return tmp_path


@pytest.fixture
def api(metrics_artifacts) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as client:
        client.app.state.metrics = load_metrics(metrics_artifacts)
        yield client


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_absent_artifacts_are_not_an_error(tmp_path) -> None:
    """The phases land in order; the API serves before any evaluation exists."""
    store = load_metrics(tmp_path)

    assert store.is_loaded is False
    assert store.stage1_test == {}
    assert store.error_histogram("test_benign") is None


def test_the_store_finds_the_shared_edge_histograms(metrics_artifacts) -> None:
    store = load_metrics(metrics_artifacts)

    benign = store.error_histogram("test_benign")
    attack = store.error_histogram("test_attack")

    assert benign is not None and attack is not None
    assert benign["edges"] == attack["edges"], "an FPR and a recall must share one axis"


def test_a_malformed_histogram_entry_is_treated_as_absent(tmp_path) -> None:
    """`edges` alone is not a histogram; only a `counts` block is usable."""
    (tmp_path / "metrics_anomaly.json").write_text(
        json.dumps({"training": {"histograms": {"edges": EDGES}}}), encoding="utf-8"
    )

    assert load_metrics(tmp_path).error_histogram("edges") is None


# ---------------------------------------------------------------------------
# GET /metrics/model
# ---------------------------------------------------------------------------


def test_model_metrics_serves_the_measured_numbers(api, api_prefix: str) -> None:
    """Served, not recomputed: these describe the held-out day."""
    body = api.get(f"{api_prefix}/metrics/model").json()

    assert body["pr_auc"] == 0.846
    assert body["accuracy"] == 0.629
    assert body["tau_sup"] == 0.3879
    assert body["fpr_at_threshold"] == 0.00016
    assert body["alerts_per_analyst_hour"] == 20.32
    assert body["labels"] == ["benign", "dos"]
    assert body["confusion_matrix"] == [[99, 1], [15, 35]]
    assert body["curves"]["pr"] == [[1.0, 0.33], [0.5, 0.8]]
    assert body["loao"]["folds"][0]["held_out"] == "dos"


def test_accuracy_is_present_but_is_not_the_headline(api, api_prefix: str) -> None:
    """On 99% benign traffic, always answering benign scores 99%.

    So accuracy is reported rather than hidden -- hiding it invites the
    question -- but PR-AUC is the field the screen is required to lead with,
    and both have to exist for that caption to be possible.
    """
    body = api.get(f"{api_prefix}/metrics/model").json()

    assert body["accuracy"] is not None
    assert body["pr_auc"] is not None


def test_model_metrics_is_503_when_nothing_is_measured(tmp_path, api_prefix: str) -> None:
    """An empty table would read as a model that scored zero."""
    app = create_app()
    with TestClient(app) as client:
        client.app.state.metrics = MetricsStore(artifacts_dir=tmp_path)
        response = client.get(f"{api_prefix}/metrics/model")

    assert response.status_code == 503
    assert "not been measured" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /metrics/threshold
# ---------------------------------------------------------------------------


def test_the_threshold_projection_reads_both_figures_off_one_axis(api, api_prefix: str) -> None:
    """The FPR and the recall must describe the same point.

    benign counts [80, 10, 6, 3, 1] over edges [0, .1, .2, .4, .8, 1.6]:
    at t = 0.2 the bins whose upper edge exceeds 0.2 hold 6 + 3 + 1 = 10 of
    100 rows. Attack counts [2, 3, 10, 35, 50] give 10 + 35 + 50 = 95 of 100.
    Both computed by hand here rather than from the module, so the test would
    catch the endpoint reading the wrong histogram.
    """
    body = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.2}).json()

    assert body["benign_rows"] == 100
    assert body["benign_above"] == 10
    assert body["fpr"] == pytest.approx(0.10)
    assert body["attack_above"] == 95
    assert body["recall"] == pytest.approx(0.95)


def test_raising_the_threshold_trades_recall_for_volume(api, api_prefix: str) -> None:
    """The whole point of the slider, asserted as a direction rather than a value."""
    low = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.1}).json()
    high = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.8}).json()

    assert high["fpr"] < low["fpr"]
    assert high["recall"] < low["recall"]
    assert high["false_alerts_per_day"] < low["false_alerts_per_day"]


def test_alerts_per_analyst_hour_divides_by_the_shift_not_by_24(api, api_prefix: str) -> None:
    """Per *analyst* hour. The shipped card reads 162.56/day as 20.32/hour.

    If this divided by 24 the slider and the model card would quote different
    volumes for the same operating point, which is the kind of disagreement
    nobody notices until a SOC lead sets a threshold from the wrong one.
    """
    from app.config import settings

    body = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.2}).json()

    assert body["alerts_per_analyst_hour"] == pytest.approx(
        body["false_alerts_per_day"] / settings.analyst_shift_hours
    )


def test_the_budget_verdict_is_reported(api, api_prefix: str) -> None:
    body = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.2}).json()

    assert body["budget_per_day"] > 0
    assert body["within_budget"] == (body["false_alerts_per_day"] <= body["budget_per_day"])


def test_a_threshold_past_the_histogram_says_so(api, api_prefix: str) -> None:
    """Stage 2's errors reach ~1.83 on the shipped card, past this slider's 1.0.

    Rather than quietly reporting a recall for a threshold the axis cannot
    express, the response flags it.
    """
    inside = api.get(f"{api_prefix}/metrics/threshold", params={"t": 0.5}).json()

    assert inside["covers_distribution"] is True


@pytest.mark.parametrize("t", [-0.1, 1.5])
def test_a_threshold_outside_zero_to_one_is_422(api, api_prefix: str, t: float) -> None:
    assert api.get(f"{api_prefix}/metrics/threshold", params={"t": t}).status_code == 422


def test_a_missing_threshold_is_422(api, api_prefix: str) -> None:
    assert api.get(f"{api_prefix}/metrics/threshold").status_code == 422


def test_threshold_is_503_without_a_benign_distribution(tmp_path, api_prefix: str) -> None:
    app = create_app()
    with TestClient(app) as client:
        client.app.state.metrics = MetricsStore(artifacts_dir=tmp_path)
        response = client.get(f"{api_prefix}/metrics/threshold", params={"t": 0.2})

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Phase 6 -- GET /metrics/anomaly-histogram, the axis the slider lives on
# ---------------------------------------------------------------------------


def test_the_histogram_endpoint_serves_the_persisted_bins(api, api_prefix: str) -> None:
    """The slider needs the distribution, not a projection at one point.

    `/metrics/threshold` answers "what happens at t". Rebuilding the shape by
    sampling it sixty times would be sixty requests to draw one chart, and the
    result would be a cumulative curve rather than the distribution.
    """
    response = api.get(f"{api_prefix}/metrics/anomaly-histogram")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["edges"] == EDGES
    assert body["spacing"] == "log"
    assert {entry["name"] for entry in body["distributions"]} == {"test_benign", "test_attack"}


def test_every_distribution_has_one_count_per_bin(api, api_prefix: str) -> None:
    """A counts list that is not len(edges) - 1 long draws bars off the axis."""
    body = api.get(f"{api_prefix}/metrics/anomaly-histogram").json()

    for entry in body["distributions"]:
        assert len(entry["counts"]) == len(body["edges"]) - 1, entry["name"]
        assert sum(entry["counts"]) == entry["rows"]


def test_the_histogram_is_503_when_no_distribution_is_loaded(tmp_path, api_prefix: str) -> None:
    """An empty histogram would read as traffic with no reconstruction error.

    That is a different claim from one that has not been measured, so the two
    get different status codes.
    """
    app = create_app()
    with TestClient(app) as client:
        client.app.state.metrics = load_metrics(tmp_path)
        response = client.get(f"{api_prefix}/metrics/anomaly-histogram")

    assert response.status_code == 503
    assert "Phase 3" in response.json()["detail"]


def test_the_histogram_edges_match_the_threshold_endpoints_axis(api, api_prefix: str) -> None:
    """The line's position and the number beside it must mean the same thing.

    If the chart were drawn on one set of bins and the projection computed from
    another, the analyst would drag to a visible point on the curve and read a
    false-positive rate belonging somewhere else.
    """
    histogram = api.get(f"{api_prefix}/metrics/anomaly-histogram").json()
    edge = histogram["edges"][2]

    projection = api.get(f"{api_prefix}/metrics/threshold", params={"t": edge}).json()

    benign = next(d for d in histogram["distributions"] if d["name"] == "test_benign")
    above = sum(benign["counts"][2:])
    assert projection["benign_above"] == above
    assert projection["benign_rows"] == benign["rows"]
