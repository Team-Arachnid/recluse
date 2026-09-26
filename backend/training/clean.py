"""Phase 1 -- CICIDS2017 cleaning.

The published dataset has specific, documented defects. Each one is handled
explicitly here rather than papered over:

1. Headers carry leading/trailing whitespace. Strip and snake_case first
   (see features.normalise_column_name).
2. flow_bytes_s and flow_packets_s contain Inf and NaN from zero-duration
   flows. Replace Inf with NaN, then decide drop vs. impute and document it.
3. Massive exact-duplicate rows. Drop before splitting. Failing to do this is
   the single largest source of inflated scores published on this dataset.
4. Zero-variance columns (bwd_psh_flags, fwd_urg_flags and others are
   all-zero). Drop programmatically and log which ones went.
5. Negative values in some duration and IAT columns. Clip at zero or drop,
   and log the count.

**The drop-vs-impute decision, recorded here because the brief asks for it:**
rows whose rate columns are non-finite are *dropped*, not imputed. Those values
come from flows with a duration of zero, so bytes-per-second is not a missing
measurement to estimate -- it is undefined. Imputing a median would invent a
throughput the flow never had, and the affected rows are a small fraction of
the file. Dropping is the honest option and the count is reported.

Output goes to data/interim as Parquet, not CSV: faster to reload and it
preserves dtypes.

The original CICIDS2017 labels contain documented errors and corrected
re-releases exist; see the README and Data-Pipeline for what that means for
the numbers this pipeline produces.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from training.console import echo
from training.features import (
    DAY_COLUMN,
    LABEL_COLUMN,
    TIMESTAMP_COLUMN,
    normalise_columns,
)

logger = logging.getLogger(__name__)

# Columns whose names contain one of these carry durations or inter-arrival
# times. Negative values in them are physically impossible and are clipped.
NEGATIVE_CLIP_COLUMN_HINTS: tuple[str, ...] = ("duration", "iat")

# Rate columns that carry Inf when a flow's duration is zero.
RATE_COLUMNS: tuple[str, ...] = ("flow_bytes_s", "flow_packets_s")


@dataclass
class CleaningReport:
    """What cleaning actually did, so the checkpoint can be reported."""

    rows_in: int = 0
    rows_out: int = 0
    infinite_values: int = 0
    nan_rows_dropped: int = 0
    duplicate_rows: int = 0
    negative_values_clipped: int = 0
    zero_variance_columns: list[str] = field(default_factory=list)

    def render(self) -> str:
        """A short block suitable for the phase checkpoint."""
        dropped = self.rows_in - self.rows_out
        pct = (dropped / self.rows_in * 100) if self.rows_in else 0.0
        lines = [
            f"rows in          {self.rows_in:,}",
            f"rows out         {self.rows_out:,}  ({dropped:,} dropped, {pct:.2f}%)",
            f"infinite values  {self.infinite_values:,}  (replaced with NaN, rows dropped)",
            f"rows with NaN    {self.nan_rows_dropped:,}",
            f"duplicate rows   {self.duplicate_rows:,}",
            f"negatives clipped {self.negative_values_clipped:,}",
            f"zero-variance    {len(self.zero_variance_columns)} column(s): "
            f"{', '.join(self.zero_variance_columns) or 'none'}",
        ]
        return "\n".join(lines)


# Stray characters that end up inside labels. Three separate provenances, one
# defect: a character wedged into a class name that splits a family in two.
#   U+0080-U+009F  C1 controls -- what the published cp1252 en dash becomes
#                  under the latin-1 read those files require
#   U+00A0         non-breaking space, in the web-attack labels as shipped
#   U+FFFD         the replacement character, baked into the widely-mirrored
#                  UTF-8 conversion where the en dash could not be decoded
_LABEL_NOISE = re.compile(r"[\u0080-\u00a0\ufffd]")
_WHITESPACE_RUN = re.compile(r"\s+")


def _clean_label(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _WHITESPACE_RUN.sub(" ", _LABEL_NOISE.sub(" ", value)).strip()


def _normalise_labels(series: pd.Series) -> pd.Series:
    """Collapse label whitespace and strip stray bytes so one family is one class.

    Three defects in the published labels, each of which splits a single attack
    family into several classes if left alone:

    * Whitespace. The web-attack labels carry non-breaking spaces and
      inconsistent runs of ordinary ones, so one family arrives as two strings.
    * A cp1252 en dash. The original files need ``encoding="latin-1"`` to read,
      and under it that byte becomes a non-printable control character sitting
      inside a class name.
    * A replacement character. The widely-mirrored UTF-8 conversion of those
      files substituted U+FFFD where the en dash could not be decoded, so the
      same family arrives spelled a third way -- and U+FFFD cannot be encoded
      to cp1252 at all, which is how printing the per-class table ends a run
      on Windows after all the work is done.

    Both matter beyond cosmetics: in the Phase 4 leave-one-attack-out loop,
    holding out one spelling of a family would leave the other in training and
    quietly invalidate the headline result.

    Applied over the distinct values rather than row-wise -- labels are
    low-cardinality and the real files run to millions of rows -- and the
    substitution is done with Python's ``re`` rather than through
    ``Series.str``, whose Arrow-backed engine rejects ``\\u`` escapes.

    Case is preserved: BENIGN is upper-case in the source and stays so.
    """
    values = series.astype(object)
    mapping = {value: _clean_label(value) for value in pd.unique(values)}
    return values.map(mapping)


def zero_variance_columns(frame: pd.DataFrame) -> list[str]:
    """Numeric columns carrying a single value, so no signal.

    The label is never included: a benign-only capture day legitimately has
    one value, and it is not a feature.
    """
    protected = (LABEL_COLUMN, TIMESTAMP_COLUMN, DAY_COLUMN)
    return [
        column
        for column in frame.columns
        if column not in protected
        and pd.api.types.is_numeric_dtype(frame[column])
        and frame[column].nunique(dropna=False) <= 1
    ]


def clean_frame(
    frame: pd.DataFrame, drop_zero_variance: bool = True
) -> tuple[pd.DataFrame, CleaningReport]:
    """Apply every documented CICIDS2017 fix, returning the frame and a report.

    Idempotent: cleaning an already-cleaned frame drops nothing further.

    ``drop_zero_variance`` exists because that particular defect is a
    property of the whole dataset rather than of one file. A column can be
    constant on Monday and vary on Wednesday -- in the published release,
    ``fwd_urg_flags`` and ``cwe_flag_count`` do exactly that -- so deciding
    per file gives the files different column sets, and concatenating those
    injects NaN into every row from a file that kept the column. The
    per-file path therefore passes ``False`` and the assessment happens once,
    globally, in ``split.load_interim``.
    """
    if frame.empty:
        raise ValueError("refusing to clean a frame with no rows")

    frame = frame.copy()
    frame.columns = normalise_columns(list(frame.columns))

    if LABEL_COLUMN not in frame.columns:
        raise ValueError(f"no {LABEL_COLUMN!r} column: this is not a CICIDS2017 flow file")

    report = CleaningReport(rows_in=len(frame))

    frame[LABEL_COLUMN] = _normalise_labels(frame[LABEL_COLUMN])

    if TIMESTAMP_COLUMN in frame.columns:
        frame[TIMESTAMP_COLUMN] = pd.to_datetime(
            frame[TIMESTAMP_COLUMN], dayfirst=True, errors="coerce"
        )

    numeric_columns = [
        column
        for column in frame.select_dtypes(include=[np.number]).columns
        if column != LABEL_COLUMN
    ]

    # 2. Inf from zero-duration flows -> NaN, then the rows go.
    if numeric_columns:
        infinite_mask = np.isinf(frame[numeric_columns].to_numpy(dtype="float64"))
        report.infinite_values = int(infinite_mask.sum())
        if report.infinite_values:
            frame[numeric_columns] = frame[numeric_columns].replace([np.inf, -np.inf], np.nan)

    # 5. Negative durations and inter-arrival times are impossible; clip them.
    clip_columns = [
        column
        for column in numeric_columns
        if any(hint in column for hint in NEGATIVE_CLIP_COLUMN_HINTS)
    ]
    for column in clip_columns:
        negative = frame[column] < 0
        count = int(negative.sum())
        if count:
            report.negative_values_clipped += count
            frame.loc[negative, column] = 0

    # 2 (continued). Drop what is left non-finite rather than inventing values.
    before = len(frame)
    frame = frame.dropna()
    report.nan_rows_dropped = before - len(frame)

    if frame.empty:
        raise ValueError("every row was dropped during cleaning; check the input")

    # 4. Zero-variance columns carry no signal. Never the label: a benign-only
    #    day legitimately has one value.
    if drop_zero_variance:
        zero_variance = zero_variance_columns(frame)
        if zero_variance:
            report.zero_variance_columns = zero_variance
            frame = frame.drop(columns=zero_variance)

    # 3. Exact duplicates, before any splitting happens.
    before = len(frame)
    frame = frame.drop_duplicates()
    report.duplicate_rows = before - len(frame)

    frame = frame.reset_index(drop=True)
    report.rows_out = len(frame)
    return frame, report


WEEKDAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def day_from_filename(path: Path) -> str | None:
    """Recover the capture day from a published file name.

    The MachineLearningCSV release ships no Timestamp column, so the day -- the
    only thing the temporal split needs -- survives nowhere else. The published
    names carry it: ``Monday-WorkingHours.pcap_ISCX.csv``,
    ``Friday-WorkingHours-Afternoon-PortScan.pcap_ISCX.csv``. Matching is
    case-insensitive because the set spells Wednesday's file with a lower-case
    ``w`` in the middle.

    Returns ``None`` rather than guessing when nothing matches: splitting on a
    wrong day is worse than refusing to split.
    """
    stem = path.name.lower()
    for day in WEEKDAYS:
        if stem.startswith(day.lower()):
            return day
    return None


def read_flow_csv(path: Path) -> pd.DataFrame:
    """Read one published CSV, picking the encoding rather than assuming one.

    The original CICIDS2017 files are cp1252/latin-1 and fail to decode as
    UTF-8. The corrected re-releases the brief mentions are UTF-8, and reading
    *those* as latin-1 silently turns every multi-byte character into two --
    a label gains a stray letter and becomes its own class, which would split
    an attack family in the counts and in the Phase 4 hold-out loop.

    UTF-8 is strict enough to fail loudly on a latin-1 file, so trying it first
    and falling back is safe in a way the reverse order is not.
    """
    try:
        return pd.read_csv(path, low_memory=False, encoding="utf-8")
    except UnicodeDecodeError:
        logger.debug("%s is not UTF-8; reading as latin-1", path.name)
        return pd.read_csv(path, low_memory=False, encoding="latin-1")


def raw_sources(raw_dir: Path) -> list[Path]:
    """Every published CSV under ``raw_dir``, in a stable order."""
    sources = sorted(raw_dir.rglob("*.csv"))
    if not sources:
        raise SystemExit(
            f"no CSV files under {raw_dir}. Download CICIDS2017 (MachineLearningCSV) "
            "from https://www.unb.ca/cic/datasets/ids-2017.html and unpack it there."
        )
    return sources


def load_raw(raw_dir: Path) -> pd.DataFrame:
    """Concatenate the published CSVs into one raw frame.

    The files share a schema but not their column *spelling* -- the published
    set differs in leading whitespace between days -- so headers are normalised
    per file before concatenating, or the union would gain duplicate columns.
    """
    frames = []
    for source in raw_sources(raw_dir):
        frame = read_flow_csv(source)
        frame.columns = normalise_columns(list(frame.columns))

        day = day_from_filename(source)
        if day is None:
            logger.warning(
                "%s: no capture day in the file name; its rows cannot be split "
                "temporally and will be dropped unless the file carries a timestamp",
                source.name,
            )
        elif TIMESTAMP_COLUMN not in frame.columns:
            frame[DAY_COLUMN] = day

        frames.append(frame)
        logger.info("read %s (%d rows, day=%s)", source.name, len(frame), day)
    return pd.concat(frames, ignore_index=True)


def clean_file(path: Path, drop_zero_variance: bool = True) -> tuple[pd.DataFrame, CleaningReport]:
    """Read one published CSV and clean it."""
    return clean_frame(read_flow_csv(path), drop_zero_variance=drop_zero_variance)


def clean_directory(raw_dir: Path, interim_dir: Path) -> CleaningReport:
    """Clean every CSV in ``raw_dir`` into one Parquet file per input."""
    sources = raw_sources(raw_dir)

    interim_dir.mkdir(parents=True, exist_ok=True)
    combined = CleaningReport()

    for source in sources:
        frame, report = clean_file(source, drop_zero_variance=False)

        # The split is temporal, and this release carries no timestamp, so the
        # day recovered from the file name is the only key it will have.
        day = day_from_filename(source)
        if day is None:
            logger.warning("%s: no capture day in the file name", source.name)
        elif TIMESTAMP_COLUMN not in frame.columns:
            frame[DAY_COLUMN] = day

        destination = interim_dir / f"{source.stem.replace(' ', '_')}.parquet"
        frame.to_parquet(destination, index=False)
        logger.info("cleaned %s -> %s", source.name, destination.name)
        echo(f"\n{source.name}\n{report.render()}")

        combined.rows_in += report.rows_in
        combined.rows_out += report.rows_out
        combined.infinite_values += report.infinite_values
        combined.nan_rows_dropped += report.nan_rows_dropped
        combined.duplicate_rows += report.duplicate_rows
        combined.negative_values_clipped += report.negative_values_clipped
        for column in report.zero_variance_columns:
            if column not in combined.zero_variance_columns:
                combined.zero_variance_columns.append(column)

    return combined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clean.py", description="Clean the published CICIDS2017 CSVs into Parquet."
    )
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--interim-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    raw_dir = args.raw_dir or settings.data_path / "raw"
    interim_dir = args.interim_dir or settings.data_path / "interim"

    combined = clean_directory(raw_dir, interim_dir)
    echo(f"\n{'=' * 60}\nall files\n{combined.render()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
