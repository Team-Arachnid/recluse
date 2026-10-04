"""Phase 5 -- per-alert explanation.

Two explainers, one per stage, chosen deliberately:

* Stage 1 (tree model): TreeSHAP, top-5 contributing features.
* Stage 2 (autoencoder): per-feature reconstruction error, top-5. The model
  hands this over for free -- the features it failed hardest to reconstruct
  are precisely why the row looks anomalous:

      per_feature_error = (x - x_hat) ** 2
      top_contributors  = argsort(per_feature_error)[-5:]

  KernelSHAP on a neural net is slow, approximate, and buys nothing here.

Every alert carries an explanation. An alert with a score and no reason is an
alert an analyst ignores.

A third function, ``narrate``, turns either explanation into one English
sentence from a static per-feature phrase map -- the same reviewed-table idiom
as ``app/mitre.py`` and ``app/remediation.py``. It renders above the bar chart
in the Alert Detail drawer, because a chart requires interpretation and a
sentence does not.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# 1. Stage 1 -- TreeSHAP
# ---------------------------------------------------------------------------


def explain_supervised(
    model: Any,
    matrix: np.ndarray,
    feature_order: list[str],
    classes: list[str],
    families: list[str | None],
    k: int = 5,
) -> list[dict[str, Any]]:
    """TreeSHAP attribution for the rows Stage 1 alerted on.

    ``matrix`` is only the alerting rows -- the caller (the Phase 5 pipeline)
    masks before calling, never the whole scored batch. Each row is attributed
    against the column of the family it was actually assigned,
    ``families[i]``, rather than whichever class the row's own SHAP values
    happen to favour most: the two can disagree, and attributing against the
    wrong column produces a confident, fluent explanation for a different
    alert, which nothing downstream can detect.

    The model may be the champion's dataclass wrapper
    (``training.estimators.LightGBMClassifier``, holding a raw ``lgb.Booster``
    in ``.booster``) or a bare sklearn estimator (the RF alternative).
    ``shap.TreeExplainer`` understands the Booster and the sklearn estimator
    but not the dataclass, so the Booster is unwrapped when present rather
    than branching on an algorithm name.
    """
    if len(matrix) == 0:
        return []
    if len(families) != len(matrix):
        raise ValueError(
            f"{len(families)} famil(y/ies) against {len(matrix)} row(s); "
            "explain_supervised needs exactly one family per row."
        )
    for index, family in enumerate(families):
        if family is None or family not in classes:
            raise ValueError(
                f"row {index} carries family {family!r}, which is not one of "
                f"{classes}. explain_supervised attributes against the named "
                "family's column; a missing or unrecognised family means the "
                "caller handed a Stage 2 (or unscored) row to the Stage 1 "
                "explainer, and guessing a column would silently explain the "
                "wrong class."
            )

    # Imported here, not at module scope: shap pulls in numba, and a process
    # that never explains a Stage 1 alert should not pay for that import --
    # the same reason app/inference.py imports torch lazily.
    import shap

    # One explainer and one batched call over every alerting row -- building
    # a fresh TreeExplainer or calling shap_values() per row would be the same
    # mistake as per-row predict().
    unwrapped = model.booster if hasattr(model, "booster") else model
    explainer = shap.TreeExplainer(unwrapped)
    raw = explainer.shap_values(matrix)

    # shap_values returns a list of (n, d) arrays, one per class, on some
    # shap/model combinations, and a single (n, d, n_classes) array on
    # others. Normalised to the latter here, once, so every row below can
    # index [:, :, class_index] without caring which shape this particular
    # combination produced.
    values = np.stack(raw, axis=-1) if isinstance(raw, list) else np.asarray(raw)

    # The one assertion this function cannot skip. Picking the wrong axis
    # produces a fluent explanation for a different class, and nothing
    # downstream -- not the chart, not the analyst -- can tell the difference,
    # so a shape this code does not recognise has to raise rather than guess.
    expected_shape = (len(matrix), len(feature_order), len(classes))
    if values.shape != expected_shape:
        raise ValueError(
            f"shap_values returned shape {values.shape}, expected "
            f"{expected_shape} (rows, features, classes). This shap/model "
            "combination produced a layout this function does not recognise, "
            "and guessing which axis is which is exactly the failure this "
            "check exists to prevent."
        )

    base_values = np.atleast_1d(np.asarray(explainer.expected_value, dtype="float64"))

    records: list[dict[str, Any]] = []
    for row_index, family in enumerate(families):
        class_index = classes.index(family)
        row_values = values[row_index, :, class_index]
        total_abs = float(np.abs(row_values).sum()) or 1.0
        order = np.argsort(np.abs(row_values))[::-1][:k]
        contributors = [
            {
                "feature": feature_order[feature_index],
                # The row's feature value as it went into the model -- scaled
                # by the training RobustScaler, not the raw flow value. An
                # analyst reading "flow_duration: 2.1" without being told that
                # is a robust-scaled unit, not milliseconds, draws the wrong
                # conclusion from the bar chart.
                "value": float(matrix[row_index, feature_index]),
                "contribution": float(row_values[feature_index]),
                "share": float(abs(row_values[feature_index]) / total_abs),
            }
            for feature_index in order
        ]
        records.append(
            {
                "explainer": "treeshap",
                "base_value": float(base_values[class_index]),
                "contributors": contributors,
            }
        )
    return records


# ---------------------------------------------------------------------------
# 2. Stage 2 -- per-feature reconstruction error
# ---------------------------------------------------------------------------


def explain_anomaly(
    model: Any,
    matrix: np.ndarray,
    feature_order: list[str],
    k: int = 5,
) -> list[dict[str, Any]]:
    """Per-feature reconstruction error for the rows Stage 2 alerted on.

    Reuses ``training.autoencoder.per_feature_error`` and ``top_contributors``
    rather than re-deriving ``(x - x_hat) ** 2`` and its ranking here: a second
    copy of the formula is a second thing that can drift from the score it
    explains, and these two already produce the exact ``{"feature", "error",
    "share"}`` shape ``train_autoencoder.family_explanations`` writes into
    ``metrics_anomaly.json`` for the per-family aggregate -- one shape for a
    per-family explanation and a per-alert one.
    """
    if len(matrix) == 0:
        return []

    # Imported here, not at module scope, for the same reason app/inference.py
    # imports torch lazily: a process serving Stage 1 alone should not pay for
    # it, and training.autoencoder pulls torch in at its own module scope.
    from training.autoencoder import per_feature_error, top_contributors

    errors = per_feature_error(model, matrix)
    return [
        {
            "explainer": "reconstruction_error",
            "contributors": top_contributors(row_errors, feature_order, k=k),
        }
        for row_errors in errors
    ]


# ---------------------------------------------------------------------------
# 3. The narrator
#
# A static per-feature phrase map, reviewed rather than generated -- the same
# idiom as app/mitre.py's TECHNIQUES and app/remediation.py's PLAYBOOKS. Every
# sentence narrate() can produce is readable straight out of this table.
#
# Covers the full CICIDS2017 feature vocabulary training/features.py produces:
# the ~69 numeric flow features plus the 3 port_group_* buckets and 20
# port_is_* indicators (92 names total; see the persisted feature_order in
# backend/artifacts/preprocessing.pkl). A name this table does not cover falls
# back to a humanised identifier rather than a guess -- see _phrase_for.
# ---------------------------------------------------------------------------

# How many of the ordered contributors a sentence names. The brief's own two
# illustrations each name three; beyond that a single sentence stops being
# something an analyst reads in four seconds.
_SENTENCE_FEATURE_LIMIT = 3

FEATURE_PHRASES: dict[str, str] = {
    # -- Duration & packet counts --------------------------------------
    "flow_duration": "the flow's duration",
    "total_fwd_packets": "the number of forward packets",
    "total_backward_packets": "the number of backward packets",
    # -- Packet size: totals, extremes, mean, spread --------------------
    "total_length_of_fwd_packets": "the total size of forward packets",
    "total_length_of_bwd_packets": "the total size of backward packets",
    "fwd_packet_length_max": "the largest forward packet",
    "fwd_packet_length_min": "the smallest forward packet",
    "fwd_packet_length_mean": "mean forward packet size",
    "fwd_packet_length_std": "the variability in forward packet size",
    "bwd_packet_length_max": "the largest backward packet",
    "bwd_packet_length_min": "the smallest backward packet",
    "bwd_packet_length_mean": "mean backward packet size",
    "bwd_packet_length_std": "the variability in backward packet size",
    # -- Flow-level byte/packet rate -------------------------------------
    "flow_bytes_s": "the flow's byte rate",
    "flow_packets_s": "the flow's packet rate",
    # -- Inter-arrival time: whole flow, then forward, then backward ----
    "flow_iat_mean": "the mean time between packets",
    "flow_iat_std": "the variability in time between packets",
    "flow_iat_max": "the longest gap between packets",
    "flow_iat_min": "the shortest gap between packets",
    "fwd_iat_total": "the total time between forward packets",
    "fwd_iat_mean": "the mean time between forward packets",
    "fwd_iat_std": "the variability in time between forward packets",
    "fwd_iat_max": "the longest gap between forward packets",
    "fwd_iat_min": "the shortest gap between forward packets",
    "bwd_iat_total": "the total time between backward packets",
    "bwd_iat_mean": "the mean time between backward packets",
    "bwd_iat_std": "the variability in time between backward packets",
    "bwd_iat_max": "the longest gap between backward packets",
    "bwd_iat_min": "the shortest gap between backward packets",
    # -- Flags and header length, forward/backward -----------------------
    "fwd_psh_flags": "the number of PSH flags on forward packets",
    "fwd_urg_flags": "the number of URG flags on forward packets",
    "fwd_header_length": "the forward header length",
    "bwd_header_length": "the backward header length",
    "fwd_packets_s": "the forward packet rate",
    "bwd_packets_s": "the backward packet rate",
    # -- Packet size over the whole flow ----------------------------------
    "min_packet_length": "the smallest packet in the flow",
    "max_packet_length": "the largest packet in the flow",
    "packet_length_mean": "the mean packet size",
    "packet_length_std": "the variability in packet size",
    "packet_length_variance": "the variance in packet size",
    # -- TCP flag counts over the whole flow -------------------------------
    "fin_flag_count": "the number of FIN flags",
    "syn_flag_count": "the number of SYN flags",
    "rst_flag_count": "the number of RST flags",
    "psh_flag_count": "the number of PSH flags",
    "ack_flag_count": "the number of ACK flags",
    "urg_flag_count": "the number of URG flags",
    "cwe_flag_count": "the number of CWE flags",
    "ece_flag_count": "the number of ECE flags",
    # -- Ratios and averages ----------------------------------------------
    "down_up_ratio": "the down/up ratio",
    "average_packet_size": "the average packet size",
    "avg_fwd_segment_size": "the average forward segment size",
    "avg_bwd_segment_size": "the average backward segment size",
    # CICFlowMeter's published CSVs carry "Fwd Header Length" twice; this is
    # the second copy, kept distinct from fwd_header_length above so the two
    # never silently read as the same evidence in a contributor list.
    "fwd_header_length_1": "a duplicate reading of the forward header length",
    # -- Subflow and TCP window --------------------------------------------
    "subflow_fwd_packets": "the number of forward packets per subflow",
    "subflow_fwd_bytes": "the number of forward bytes per subflow",
    "subflow_bwd_packets": "the number of backward packets per subflow",
    "subflow_bwd_bytes": "the number of backward bytes per subflow",
    "init_win_bytes_forward": "the initial forward window size",
    "init_win_bytes_backward": "the initial backward window size",
    "act_data_pkt_fwd": "the number of forward packets carrying data",
    "min_seg_size_forward": "the minimum forward segment size",
    # -- Active/idle burst timing -------------------------------------------
    "active_mean": "the mean active-burst duration",
    "active_std": "active-time variability",
    "active_max": "the longest active burst",
    "active_min": "the shortest active burst",
    "idle_mean": "the mean idle-gap duration",
    "idle_std": "the variability in idle-gap duration",
    "idle_max": "the longest idle gap",
    "idle_min": "the shortest idle gap",
    # -- Destination port: IANA-range buckets -------------------------------
    "port_group_well_known": "a well-known destination port",
    "port_group_registered": "a registered destination port",
    "port_group_ephemeral": "an ephemeral destination port",
    # -- Destination port: the top-20 specific ports seen in training ------
    "port_is_53": "traffic to port 53 (DNS)",
    "port_is_80": "traffic to port 80 (HTTP)",
    "port_is_443": "traffic to port 443 (HTTPS)",
    "port_is_123": "traffic to port 123 (NTP)",
    "port_is_21": "traffic to port 21 (FTP control)",
    "port_is_22": "traffic to port 22 (SSH)",
    "port_is_389": "traffic to port 389 (LDAP)",
    "port_is_88": "traffic to port 88 (Kerberos)",
    "port_is_137": "traffic to port 137 (NetBIOS Name Service)",
    "port_is_465": "traffic to port 465 (SMTP over TLS)",
    "port_is_3268": "traffic to port 3268 (LDAP Global Catalog)",
    "port_is_139": "traffic to port 139 (NetBIOS Session Service)",
    "port_is_445": "traffic to port 445",
    "port_is_0": "traffic to port 0, a reserved value",
    "port_is_138": "traffic to port 138 (NetBIOS Datagram Service)",
    "port_is_135": "traffic to port 135 (Microsoft RPC)",
    # Windows assigns these dynamically; there is no fixed service to name,
    # and naming one would be pretending to know more than the table does.
    "port_is_49666": "traffic to an ephemeral port (49666)",
    "port_is_5353": "traffic to port 5353 (mDNS)",
    "port_is_5355": "traffic to port 5355 (LLMNR)",
    "port_is_49671": "traffic to an ephemeral port (49671)",
}


def _phrase_for(feature: str) -> str:
    """The noun phrase an analyst would say for one feature.

    Falls back to humanising the identifier -- underscores to spaces -- for a
    name the table does not cover. The fallback stays plain on purpose: it
    must not imply more understanding of the feature than the table actually
    has.
    """
    return FEATURE_PHRASES.get(feature, feature.replace("_", " "))


def _join(phrases: list[str], conjunction: str = "and") -> str:
    """An Oxford-comma English list: ``"a"``, ``"a and b"``, ``"a, b, and c"``."""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} {conjunction} {phrases[1]}"
    return f"{', '.join(phrases[:-1])}, {conjunction} {phrases[-1]}"


def _port_clause(flow: dict[str, Any] | None, chosen: list[dict[str, Any]]) -> str:
    """An observed, unscaled destination port, only when the sentence is about one.

    ``flow`` carries values as they arrived, not the scaled ones a contributor's
    own ``value`` holds -- useful for a port number, which means nothing after
    a ``RobustScaler``. Added only when a port feature is actually among the
    sentence's contributors, so handing over a flow record does not pad every
    sentence with a port nobody asked about.
    """
    if not flow:
        return ""
    if not any(contributor["feature"].startswith("port_") for contributor in chosen):
        return ""
    port = flow.get("destination_port")
    return f" Observed destination port: {port}." if port is not None else ""


def _narrate_known(
    family: str, contributors: list[dict[str, Any]], flow: dict[str, Any] | None
) -> str:
    """The KNOWN register: a named family, direction from the sign of contribution."""
    label = family.replace("_", " ")
    chosen = contributors[:_SENTENCE_FEATURE_LIMIT]
    toward = [_phrase_for(c["feature"]) for c in chosen if c["contribution"] >= 0]
    against = [_phrase_for(c["feature"]) for c in chosen if c["contribution"] < 0]

    if toward and against:
        sentence = (
            f"Classified as {label}: {_join(toward)} pushed the score toward this "
            f"classification, while {_join(against)} pushed against it."
        )
    elif against:
        # Rare, but honest: the largest-magnitude evidence argued against this
        # family and the model still chose it on the rest of the attribution.
        sentence = (
            f"Classified as {label}: the model weighted {_join(against)} against "
            "this classification, but it was outweighed by the rest of the evidence."
        )
    else:
        sentence = f"Classified as {label}: {_join(toward)} are what the model weighted."

    return sentence + _port_clause(flow, chosen)


def _narrate_anomaly(contributors: list[dict[str, Any]], flow: dict[str, Any] | None) -> str:
    """The UNCLASSIFIED_ANOMALY register: no family, no sign, only ``share``."""
    chosen = contributors[:_SENTENCE_FEATURE_LIMIT]
    phrases = [_phrase_for(c["feature"]) for c in chosen]
    pct = round(sum(float(c["share"]) for c in chosen) * 100)

    sentence = (
        "Does not match normal traffic: the model could not reproduce "
        f"{_join(phrases, conjunction='or')} -- together {pct}% of why this flow scored."
    )
    return sentence + _port_clause(flow, chosen)


def narrate(
    kind: str,
    family: str | None,
    contributors: list[dict[str, Any]],
    flow: dict[str, Any] | None = None,
) -> str:
    """One English sentence from an explanation, for the top of the Alert Detail drawer.

    Templated from the static ``FEATURE_PHRASES`` table above rather than
    generated free-form, so a reviewer can check every sentence this function
    is capable of producing by reading the table instead of trusting a
    model's prose.

    The two kinds get different registers because they are different claims.
    ``KNOWN`` has a named family and signed contributions, so the sentence
    says what pushed the score toward -- or against -- that family.
    ``UNCLASSIFIED_ANOMALY`` has neither: Stage 2's contributors carry an
    unsigned ``error`` and a ``share`` rather than a ``contribution``, so the
    sentence says what the model could not reconstruct and leans on ``share``
    to make that magnitude readable. It must never say "technique" -- that is
    panel 2's claim, built from ``app.mitre``, and this panel does not get to
    make it first.

    An empty ``contributors`` list returns a sentence saying the explanation
    is unavailable, rather than raising or truncating mid-sentence: Task 6's
    pipeline only calls this for rows that alerted, but an explainer that
    returned nothing for one is a caller bug this function should survive,
    not propagate.
    """
    if not contributors:
        return "No explanation is available for this alert."

    if kind == "KNOWN":
        if not family:
            raise ValueError(
                "narrate() got kind='KNOWN' with no family. A KNOWN alert always "
                "carries the family Stage 1 named -- app.models.Alert enforces "
                "that with its family_matches_kind constraint -- so a missing "
                "one here means the caller lost it, not that there is nothing "
                "to say."
            )
        return _narrate_known(family, contributors, flow)

    if kind == "UNCLASSIFIED_ANOMALY":
        return _narrate_anomaly(contributors, flow)

    raise ValueError(
        f"unknown alert kind {kind!r}; expected one of app.models.ALERT_KINDS "
        "('KNOWN', 'UNCLASSIFIED_ANOMALY')."
    )
