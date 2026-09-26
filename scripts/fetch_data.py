#!/usr/bin/env python3
"""Fetch the CICIDS2017 flow CSVs into data/raw/.

The dataset's own distribution point at unb.ca is behind a licence form, so it
cannot be scripted. A Kaggle mirror of the same MachineLearningCSV release can
be, and that is what this does.

    python scripts/fetch_data.py            # download and link into data/raw
    python scripts/fetch_data.py --copy     # copy instead of linking
    python scripts/fetch_data.py --where    # just print the cache path

Two things worth knowing about the mirror before trusting numbers from it:

* It is the **MachineLearningCSV** release, which CIC published with Flow ID,
  the source and destination IPs, Source Port and Timestamp already removed.
  Most of the leakage deny-list is therefore moot here, and the temporal split
  recovers the capture day from the file names instead of a timestamp.
* Its web-attack labels contain U+FFFD where the original cp1252 en dash was.
  Whoever converted the files to UTF-8 replaced the byte rather than decoding
  it. `training.clean` normalises that away, along with the original dash and
  the non-breaking spaces, so one family stays one class.

kagglehub is not a project dependency -- nothing at runtime needs it -- so it
is installed on demand.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET = "chethuhn/network-intrusion-dataset"


def _install_kagglehub() -> None:
    """Install kagglehub into whichever environment is running this.

    The backend venv is uv-managed and ships no pip, so `python -m pip` fails
    there. `uv pip install` targets the active venv and is tried first.
    """
    attempts = (
        ["uv", "pip", "install", "--quiet", "--python", sys.executable, "kagglehub"],
        [sys.executable, "-m", "pip", "install", "--quiet", "kagglehub"],
    )
    for command in attempts:
        try:
            subprocess.run(command, check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise SystemExit(
        "could not install kagglehub automatically. Install it yourself and re-run:\n"
        "    uv pip install kagglehub\n"
        "or download the CSVs by hand from\n"
        "    https://www.kaggle.com/datasets/chethuhn/network-intrusion-dataset\n"
        "and unpack them into data/raw/."
    )


def download() -> Path:
    try:
        import kagglehub
    except ImportError:
        print("installing kagglehub...", flush=True)
        _install_kagglehub()
        import kagglehub

    return Path(kagglehub.dataset_download(DATASET))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fetch_data.py", description="Download CICIDS2017 into data/raw/."
    )
    parser.add_argument("--copy", action="store_true", help="Copy the CSVs instead of linking")
    parser.add_argument("--where", action="store_true", help="Print the cache path and exit")
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    args = parser.parse_args(argv)

    source = download()
    if args.where:
        print(source)
        return 0

    csvs = sorted(source.glob("*.csv"))
    if not csvs:
        raise SystemExit(f"no CSVs under {source}; the mirror's layout may have changed")

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    for csv in csvs:
        target = args.raw_dir / csv.name
        if target.exists() or target.is_symlink():
            continue
        if args.copy:
            shutil.copy2(csv, target)
        else:
            try:
                target.symlink_to(csv)
            except OSError:
                # Windows needs developer mode or elevation for symlinks.
                shutil.copy2(csv, target)

    total = sum(c.stat().st_size for c in csvs)
    print(f"{len(csvs)} file(s), {total / 1e6:.0f} MB, ready in {args.raw_dir}")
    print("next: make data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
