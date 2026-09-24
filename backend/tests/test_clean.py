"""Phase 1 cleaning: the five documented CICIDS2017 defects.

Each test here corresponds to a defect the brief names explicitly. They run
against a synthetic frame shaped like the published CSVs rather than the real
download, so the suite stays runnable without a 500MB dataset — but every
defect the fixture carries is one the real files are documented to contain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.clean import (
    NEGATIVE_CLIP_COLUMN_HINTS,
    CleaningReport,
    clean_frame,
    read_flow_csv,
)
from training.features import LABEL_COLUMN


def test_headers_are_stripped_and_snake_cased(raw_cicids_frame: pd.DataFrame) -> None:
    """The published CSVs ship headers with leading whitespace."""
    cleaned, _ = clean_frame(raw_cicids_frame)

    assert " Flow Duration" not in cleaned.columns
    assert "flow_duration" in cleaned.columns
    assert "flow_bytes_s" in cleaned.columns
    assert "label" in cleaned.columns


def test_infinite_rates_do_not_survive(raw_cicids_frame: pd.DataFrame) -> None:
    """Zero-duration flows put Inf in flow_bytes_s and flow_packets_s."""
    cleaned, report = clean_frame(raw_cicids_frame)

    numeric = cleaned.select_dtypes(include=[np.number])
    assert not np.isinf(numeric.to_numpy()).any()
    assert report.infinite_values > 0


def test_no_nan_survives_cleaning(raw_cicids_frame: pd.DataFrame) -> None:
    """The phase checkpoint requires this explicitly."""
    cleaned, _ = clean_frame(raw_cicids_frame)

    assert not cleaned.isna().to_numpy().any()


def test_exact_duplicate_rows_are_dropped(raw_cicids_frame: pd.DataFrame) -> None:
    """The single largest source of inflated scores published on this dataset."""
    cleaned, report = clean_frame(raw_cicids_frame)

    assert report.duplicate_rows > 0
    assert not cleaned.duplicated().any()


def test_zero_variance_columns_are_dropped_and_named(
    raw_cicids_frame: pd.DataFrame,
) -> None:
    """bwd_psh_flags and fwd_urg_flags are all-zero in the real files."""
    cleaned, report = clean_frame(raw_cicids_frame)

    assert "bwd_psh_flags" in report.zero_variance_columns
    assert "bwd_psh_flags" not in cleaned.columns


def test_the_label_column_is_never_treated_as_zero_variance() -> None:
    """A benign-only day has one label value; that is not a column to drop."""
    frame = pd.DataFrame(
        {
            " Flow Duration": [1, 2, 3],
            " Timestamp": ["3/7/2017 09:00", "3/7/2017 09:01", "3/7/2017 09:02"],
            " Label": ["BENIGN", "BENIGN", "BENIGN"],
        }
    )

    cleaned, report = clean_frame(frame)

    assert "label" in cleaned.columns
    assert "label" not in report.zero_variance_columns


def test_negative_durations_are_clipped_at_zero(raw_cicids_frame: pd.DataFrame) -> None:
    """Some duration and IAT columns carry negative values."""
    cleaned, report = clean_frame(raw_cicids_frame)

    assert report.negative_values_clipped > 0
    for column in cleaned.columns:
        if any(hint in column for hint in NEGATIVE_CLIP_COLUMN_HINTS):
            assert (cleaned[column] >= 0).all()


def test_timestamp_is_parsed_and_kept_for_splitting(
    raw_cicids_frame: pd.DataFrame,
) -> None:
    """Timestamp survives cleaning; split.py is what drops it."""
    cleaned, _ = clean_frame(raw_cicids_frame)

    assert "timestamp" in cleaned.columns
    assert pd.api.types.is_datetime64_any_dtype(cleaned["timestamp"])


def test_labels_are_normalised_but_case_is_preserved(
    raw_cicids_frame: pd.DataFrame,
) -> None:
    """Labels carry stray whitespace and non-breaking spaces in the real files."""
    cleaned, _ = clean_frame(raw_cicids_frame)

    assert "BENIGN" in set(cleaned["label"])
    assert not any(label != label.strip() for label in cleaned["label"])


def test_report_counts_rows_in_and_out(raw_cicids_frame: pd.DataFrame) -> None:
    cleaned, report = clean_frame(raw_cicids_frame)

    assert report.rows_in == len(raw_cicids_frame)
    assert report.rows_out == len(cleaned)
    assert report.rows_out < report.rows_in


def test_report_renders_a_readable_summary(raw_cicids_frame: pd.DataFrame) -> None:
    _, report = clean_frame(raw_cicids_frame)

    rendered = report.render()

    assert "rows in" in rendered
    assert str(report.rows_in) in rendered


def test_cleaning_is_idempotent(raw_cicids_frame: pd.DataFrame) -> None:
    """Cleaning an already-clean frame must not drop anything further."""
    once, _ = clean_frame(raw_cicids_frame)
    twice, second_report = clean_frame(once)

    assert len(twice) == len(once)
    assert second_report.duplicate_rows == 0


def test_empty_frame_is_rejected_rather_than_silently_passed() -> None:
    with pytest.raises(ValueError, match="no rows"):
        clean_frame(pd.DataFrame({" Flow Duration": [], " Label": []}))


def test_missing_label_column_is_rejected() -> None:
    """A frame with no label is not a CICIDS2017 file and must not proceed."""
    frame = pd.DataFrame({" Flow Duration": [1, 2], " Timestamp": ["3/7/2017 09:00"] * 2})

    with pytest.raises(ValueError, match="label"):
        clean_frame(frame)


def test_cleaning_report_is_a_dataclass_with_dropped_columns(
    raw_cicids_frame: pd.DataFrame,
) -> None:
    _, report = clean_frame(raw_cicids_frame)

    assert isinstance(report, CleaningReport)
    assert isinstance(report.zero_variance_columns, list)


def test_labels_differing_only_by_whitespace_become_one_class() -> None:
    """The real files carry non-breaking spaces inside web-attack labels.

    Left alone, "Web Attack  XSS" and "Web Attack XSS" are two classes, which
    silently doubles an attack family in the per-class counts this phase has
    to report.
    """
    frame = pd.DataFrame(
        {
            " Flow Duration": [1, 2, 3],
            " Timestamp": ["3/7/2017 09:00", "3/7/2017 09:01", "3/7/2017 09:02"],
            " Label": [
                "Web Attack \u00a0 XSS",
                "Web  Attack   XSS",
                "Web Attack XSS",
            ],
        }
    )

    cleaned, _ = clean_frame(frame)

    assert set(cleaned[LABEL_COLUMN]) == {"Web Attack XSS"}


def test_control_bytes_inside_labels_are_normalised_away() -> None:
    """The published web-attack labels carry a cp1252 en dash.

    Read as latin-1 -- which is what the real files need -- that byte becomes
    U+0096, a non-printable control character sitting inside a class name. It
    renders as a replacement glyph in the per-class table this phase has to
    report, and any file saved in a different encoding produces a *different*
    stray byte for the same family, splitting one attack into several classes.
    """
    frame = pd.DataFrame(
        {
            " Flow Duration": [1, 2, 3, 4],
            " Timestamp": [
                "6/7/2017 09:00",
                "6/7/2017 09:01",
                "6/7/2017 09:02",
                "6/7/2017 09:03",
            ],
            " Label": [
                "Web Attack \x96 Brute Force",
                "Web Attack \u00a0 Brute Force",
                "Web Attack  Brute Force",
                "Web Attack Brute Force",
            ],
        }
    )

    cleaned, _ = clean_frame(frame)

    assert set(cleaned[LABEL_COLUMN]) == {"Web Attack Brute Force"}


def test_a_utf8_rerelease_reads_without_mojibake(tmp_path) -> None:
    """The brief notes corrected CICIDS2017 re-releases exist.

    The original files need latin-1. A corrected re-release saved as UTF-8 read
    as latin-1 turns every multi-byte character into two, so a label picks up a
    stray letter and becomes its own class. The reader has to tell them apart.
    """
    source = tmp_path / "corrected.csv"
    source.write_text(
        " Flow Duration, Timestamp, Label\n"
        "1,6/7/2017 09:00,Web Attack \u2013 Brute Force\n"
        "2,6/7/2017 09:01,Web Attack \u2013 Brute Force\n",
        encoding="utf-8",
    )

    frame = read_flow_csv(source)

    assert set(frame.columns) >= {" Flow Duration", " Label"}
    assert "\u00c2" not in "".join(str(v) for v in frame[" Label"])
