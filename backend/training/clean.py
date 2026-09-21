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

Output goes to data/interim as Parquet, not CSV: faster to reload and it
preserves dtypes.

Also note in the README that the original labels contain documented errors and
that corrected re-releases exist.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("clean.py is implemented in Phase 1 (data and features).")


if __name__ == "__main__":
    main()
