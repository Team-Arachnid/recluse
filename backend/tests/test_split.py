"""Phase 1 temporal splitting.

The split is by day, never shuffled. `train_test_split(shuffle=True)` scatters
near-identical duplicated flows across train and test and manufactures scores
in the high nineties that mean nothing; these tests exist to keep that from
creeping back in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.features import DAY_COLUMN, LABEL_COLUMN, TIMESTAMP_COLUMN
from training.split import (
    BENIGN_LABEL,
    DAY_ROLES,
    AttackInBenignTrainingSet,
    split_frames,
)


def test_tuesday_and_wednesday_are_training_days(clean_cicids_frame) -> None:
    result = split_frames(clean_cicids_frame)

    assert DAY_ROLES["Tuesday"] == "train"
    assert DAY_ROLES["Wednesday"] == "train"
    assert {"FTP-Patator", "SSH-Patator", "DoS Hulk"} <= set(result.train[LABEL_COLUMN])


def test_thursday_is_validation(clean_cicids_frame) -> None:
    result = split_frames(clean_cicids_frame)

    assert DAY_ROLES["Thursday"] == "val"
    assert "Infiltration" in set(result.val[LABEL_COLUMN])


def test_friday_is_test(clean_cicids_frame) -> None:
    result = split_frames(clean_cicids_frame)

    assert DAY_ROLES["Friday"] == "test"
    assert {"PortScan", "DDoS"} <= set(result.test[LABEL_COLUMN])


def test_attack_families_never_span_train_and_test(clean_cicids_frame) -> None:
    """A family in both would make the test set partly memorisable."""
    result = split_frames(clean_cicids_frame)

    train_attacks = set(result.train[LABEL_COLUMN]) - {BENIGN_LABEL}
    test_attacks = set(result.test[LABEL_COLUMN]) - {BENIGN_LABEL}

    assert not (train_attacks & test_attacks)


def test_benign_training_set_is_attack_free(clean_cicids_frame) -> None:
    """The whole Stage 2 claim rests on this file containing no attacks."""
    result = split_frames(clean_cicids_frame)

    assert set(result.benign_train[LABEL_COLUMN]) == {BENIGN_LABEL}


def test_benign_training_set_includes_benign_rows_from_training_days(
    clean_cicids_frame,
) -> None:
    """Monday in full, plus the benign rows of Tuesday and Wednesday."""
    result = split_frames(clean_cicids_frame)

    monday_rows = (clean_cicids_frame[TIMESTAMP_COLUMN].dt.day_name() == "Monday").sum()

    assert len(result.benign_train) > monday_rows


def test_an_attack_leaking_into_the_benign_set_raises(clean_cicids_frame) -> None:
    """Asserted in code, not just documented."""
    poisoned = clean_cicids_frame.copy()
    monday = poisoned[TIMESTAMP_COLUMN].dt.day_name() == "Monday"
    poisoned.loc[poisoned.index[monday][0], LABEL_COLUMN] = "DDoS"

    with pytest.raises(AttackInBenignTrainingSet):
        split_frames(poisoned)


def test_timestamp_is_dropped_from_every_split(clean_cicids_frame) -> None:
    """Kept only until splitting; never a model input."""
    result = split_frames(clean_cicids_frame)

    for frame in (result.train, result.val, result.test, result.benign_train):
        assert TIMESTAMP_COLUMN not in frame.columns


def test_no_row_is_shared_between_splits(clean_cicids_frame) -> None:
    """The acceptance criterion, asserted rather than assumed.

    Dropping the timestamp can make rows from different days identical, which
    is exactly how a duplicate ends up spanning train and test.
    """
    result = split_frames(clean_cicids_frame)

    def keys(frame: pd.DataFrame) -> set[tuple]:
        return set(map(tuple, frame.astype(str).to_numpy()))

    train, val, test = keys(result.train), keys(result.val), keys(result.test)

    assert not (train & val)
    assert not (train & test)
    assert not (val & test)


def test_cross_split_duplicates_are_counted(clean_cicids_frame) -> None:
    """A row duplicated across days is removed from the later split and logged."""
    frame = clean_cicids_frame.copy()
    thursday = frame[frame[TIMESTAMP_COLUMN].dt.day_name() == "Thursday"]
    stolen = thursday.iloc[[0]].copy()
    stolen[TIMESTAMP_COLUMN] = pd.Timestamp("2017-07-07 09:00")  # move it to Friday
    frame = pd.concat([frame, stolen], ignore_index=True)

    result = split_frames(frame)

    assert result.report.cross_split_duplicates >= 1


def test_report_gives_row_counts_per_split_per_class(clean_cicids_frame) -> None:
    """The phase checkpoint asks for exactly this table."""
    result = split_frames(clean_cicids_frame)
    counts = result.report.counts

    assert counts["train"][BENIGN_LABEL] > 0
    assert counts["test"]["DDoS"] > 0
    assert sum(counts["val"].values()) == len(result.val)


def test_report_renders_the_checkpoint_table(clean_cicids_frame) -> None:
    result = split_frames(clean_cicids_frame)

    rendered = result.report.render()

    assert "train" in rendered
    assert BENIGN_LABEL in rendered


def test_a_day_with_no_rows_produces_an_empty_split_not_a_crash() -> None:
    """Monday-only input still has to produce a usable benign training set."""
    frame = pd.DataFrame(
        {
            TIMESTAMP_COLUMN: pd.to_datetime(["2017-07-03 09:00", "2017-07-03 09:01"]),
            "flow_duration": [10, 20],
            LABEL_COLUMN: [BENIGN_LABEL, BENIGN_LABEL],
        }
    )

    result = split_frames(frame)

    assert len(result.benign_train) == 2
    assert result.train.empty
    assert result.test.empty


# ---------------------------------------------------------------------------
# Splitting without a timestamp
# ---------------------------------------------------------------------------
def test_capture_day_is_used_when_there_is_no_timestamp() -> None:
    """The MachineLearningCSV release ships no Timestamp column at all."""
    frame = pd.DataFrame(
        {
            DAY_COLUMN: ["Monday", "Tuesday", "Thursday", "Friday"],
            "flow_duration": [10, 20, 30, 40],
            LABEL_COLUMN: [BENIGN_LABEL, "FTP-Patator", "Infiltration", "DDoS"],
        }
    )

    result = split_frames(frame)

    assert set(result.train[LABEL_COLUMN]) == {"FTP-Patator"}
    assert set(result.val[LABEL_COLUMN]) == {"Infiltration"}
    assert set(result.test[LABEL_COLUMN]) == {"DDoS"}
    assert set(result.benign_train[LABEL_COLUMN]) == {BENIGN_LABEL}


def test_the_capture_day_is_dropped_from_every_split() -> None:
    """It is a splitting key, exactly like the timestamp. Never a feature."""
    frame = pd.DataFrame(
        {
            DAY_COLUMN: ["Monday", "Tuesday", "Friday"],
            "flow_duration": [10, 20, 30],
            LABEL_COLUMN: [BENIGN_LABEL, "FTP-Patator", "DDoS"],
        }
    )

    result = split_frames(frame)

    for part in (result.train, result.val, result.test, result.benign_train):
        assert DAY_COLUMN not in part.columns


def test_a_timestamp_wins_over_a_capture_day_when_both_are_present() -> None:
    """The timestamp is the finer-grained truth where a file carries one."""
    frame = pd.DataFrame(
        {
            TIMESTAMP_COLUMN: pd.to_datetime(["2017-07-07 09:00", "2017-07-07 09:01"]),
            DAY_COLUMN: ["Monday", "Monday"],
            "flow_duration": [10, 20],
            LABEL_COLUMN: ["DDoS", "Bot"],
        }
    )

    result = split_frames(frame)

    assert len(result.test) == 2
    assert result.benign_train.empty


def test_a_frame_with_neither_a_timestamp_nor_a_day_is_rejected() -> None:
    frame = pd.DataFrame({"flow_duration": [1, 2], LABEL_COLUMN: ["BENIGN", "DDoS"]})

    with pytest.raises(ValueError, match="timestamp"):
        split_frames(frame)


def test_splitting_a_large_frame_finishes_quickly() -> None:
    """Cross-split dedup must be vectorised, not row-wise.

    The real dataset is 2.5M rows by ~70 columns. A row-wise
    `astype(str).agg(join, axis=1)` to build comparison keys runs in Python per
    row and does not finish in any reasonable time at that size, which is a
    defect no small fixture can reveal.
    """
    import time

    rows = 200_000
    frame = pd.DataFrame(
        {
            DAY_COLUMN: np.resize(["Monday", "Tuesday", "Thursday", "Friday"], rows),
            LABEL_COLUMN: np.resize([BENIGN_LABEL, "DDoS"], rows),
            **{f"f{i}": np.arange(rows, dtype="float64") for i in range(20)},
        }
    )

    started = time.perf_counter()
    result = split_frames(frame)
    elapsed = time.perf_counter() - started

    assert not result.train.empty
    assert elapsed < 5, f"splitting {rows:,} rows took {elapsed:.1f}s"
