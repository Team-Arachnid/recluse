"""Phase 8 -- the model release: one trained pair, committed, verified, installed.

``backend/artifacts/`` is reproducible output and stays gitignored. It holds
every run's leftovers -- the losing algorithm's pair, the retrain arms, the
archived champions -- and it changes every time somebody trains. But "docker
compose up with models pre-loaded" cannot be met by a directory a clean clone
does not have, and rebuilding it needs the 500MB CICIDS2017 release behind a
licence form. So exactly one trained pair is committed, under
``backend/release/``, with the files the API serves beside it and a manifest of
sha256 digests.

Three rules keep that honest:

* **Never over a model somebody made.** ``install`` fills an artifacts
  directory that has nothing serving in it, and upgrades one still serving an
  earlier release exactly as installed -- a ``git pull`` that brings a new
  release should bring it into a container's volume too. Anything else that is
  serving (a model trained here, a Phase 7 promotion) wins over the release,
  because it is the more specific statement about what should run. ``--force``
  is the only way past that, and the files it displaces are moved aside rather
  than deleted.
* **Verified before anything moves.** Every digest is checked against the
  manifest, every file is staged and checked again, and only then is anything
  renamed into place. A corrupt checkout -- a pickle mangled by a line-ending
  filter, a truncated download -- is refused rather than served, and it can
  never leave a model beside a scaler from a different run, which is the one
  mismatch the schema hash cannot see when both halves share a contract.
* **The manifest records the libraries that wrote the pickles.** A LightGBM or
  scikit-learn pickle is only promised to load under the version that wrote it,
  so a mismatch is reported instead of discovered as a stack trace.

    python -m app.release status            # what is serving, what is released
    python -m app.release install           # fill an empty artifacts directory
    python -m app.release install --force   # replace whatever is serving
    python -m app.release verify            # check the release's own digests
    python -m app.release build             # snapshot the serving model (maintainers)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from app.inference import (
    AUTOENCODER_FILE,
    MODEL_CARD_FILE,
    PREPROCESSING_FILE,
    SUPERVISED_FILE,
    compose_version,
)

logger = logging.getLogger(__name__)

MANIFEST_FILE = "MANIFEST.json"
MANIFEST_FORMAT = 1

# Written into the artifacts directory by `install`, so `status` can say which
# release is serving and when it arrived.
INSTALLED_MARKER = "release.json"

# The served pair, then everything the API reads beside it.
MODEL_FILES: tuple[str, ...] = (
    SUPERVISED_FILE,
    PREPROCESSING_FILE,
    AUTOENCODER_FILE,
    MODEL_CARD_FILE,
)
EVALUATION_FILES: tuple[str, ...] = (
    "metrics_supervised.json",
    "metrics_anomaly.json",
    "metrics_loao.json",
    "drift_reference.json",
)
INSTALLED_FILES: tuple[str, ...] = MODEL_FILES + EVALUATION_FILES

# The demo flows `make seed` and the "demo" replay read. They stay in the
# release directory rather than being installed: they are input, not model.
DEMO_FLOWS_FILE = "demo_flows.parquet"
DEMO_FLOWS_CARD = "demo_flows.json"
DEMO_FILES: tuple[str, ...] = (DEMO_FLOWS_FILE, DEMO_FLOWS_CARD)

# Libraries whose version decides whether the artifacts load or score the same.
RUNTIME_PACKAGES: tuple[str, ...] = (
    "numpy",
    "pandas",
    "scikit-learn",
    "lightgbm",
    "torch",
    "shap",
)
# The subset a pickle actually depends on. torch is absent on purpose: the
# autoencoder ships as a bare state dict of tensors, read with weights_only.
PICKLE_PACKAGES: tuple[str, ...] = ("numpy", "scikit-learn", "lightgbm")

REPRODUCE = (
    "make data && make train && make train-lgbm && make train-anomaly "
    "&& make loao && make drift-reference"
)

DATASET = {
    "name": "CICIDS2017",
    "publisher": "Canadian Institute for Cybersecurity, University of New Brunswick",
    "url": "https://www.unb.ca/cic/datasets/ids-2017.html",
    "citation": (
        "Iman Sharafaldin, Arash Habibi Lashkari and Ali A. Ghorbani, "
        '"Toward Generating a New Intrusion Detection Dataset and Intrusion '
        'Traffic Characterization", 4th International Conference on Information '
        "Systems Security and Privacy (ICISSP), Portugal, January 2018."
    ),
}


class ReleaseError(RuntimeError):
    """The release is missing, malformed, or does not match its manifest."""


@dataclass
class InstallResult:
    """What `install` did, phrased for the log line the entrypoint prints."""

    action: str  # installed | upgraded | already-installed | kept
    release_version: str
    serving_version: str | None
    replaced_into: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.action == "upgraded":
            return f"upgraded release {self.serving_version} to {self.release_version}"
        if self.action == "installed":
            line = f"installed release {self.release_version}"
            if self.replaced_into is not None:
                line += f"; the files it replaced were moved to {self.replaced_into}"
            return line
        if self.action == "already-installed":
            return f"release {self.release_version} is already serving"
        return (
            f"kept {self.serving_version}, which is already serving; the release "
            f"({self.release_version}) was not installed over it. "
            "`python -m app.release install --force` replaces it."
        )


# ---------------------------------------------------------------------------
# Digests and the manifest
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_entry(path: Path) -> dict[str, Any]:
    return {"sha256": sha256_of(path), "bytes": path.stat().st_size}


def read_manifest(release_dir: Path) -> dict[str, Any]:
    path = release_dir / MANIFEST_FILE
    if not path.exists():
        raise ReleaseError(
            f"no {MANIFEST_FILE} in {release_dir}. The release is committed with the "
            "repository; a checkout without it is incomplete."
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ReleaseError(
            f"{path} is manifest format {manifest.get('format')!r}; this code reads "
            f"format {MANIFEST_FORMAT}."
        )
    missing = [name for name in INSTALLED_FILES if name not in manifest.get("files", {})]
    if missing:
        raise ReleaseError(f"{path} does not list {missing}; a release ships the full set.")
    return manifest


def check_files(directory: Path, entries: dict[str, dict[str, Any]]) -> list[str]:
    """Every way the files in `directory` disagree with `entries`, as sentences."""
    problems: list[str] = []
    for name, entry in entries.items():
        path = directory / name
        if not path.exists():
            problems.append(f"{name} is missing from {directory}")
            continue
        size = path.stat().st_size
        if size != entry["bytes"]:
            problems.append(f"{name} is {size:,} bytes; the manifest says {entry['bytes']:,}")
            continue
        actual = sha256_of(path)
        if actual != entry["sha256"]:
            problems.append(
                f"{name} has sha256 {actual[:16]}..., the manifest says {entry['sha256'][:16]}..."
            )
    return problems


def verify_release(release_dir: Path) -> dict[str, Any]:
    """The manifest, once every file it lists matches its digest. Raises otherwise."""
    manifest = read_manifest(release_dir)
    problems = check_files(release_dir, manifest["files"])
    if problems:
        raise ReleaseError(
            "the release does not match its manifest -- refusing to use it:\n  "
            + "\n  ".join(problems)
        )
    return manifest


def runtime_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in RUNTIME_PACKAGES:
        try:
            versions[name] = package_version(name)
        except PackageNotFoundError:
            versions[name] = "absent"
    return versions


def _minor(version: str) -> str:
    """'2.14.1+cpu' -> '2.14': the part a pickle's compatibility follows."""
    return ".".join(version.split("+", 1)[0].split(".")[:2])


def runtime_warnings(manifest: dict[str, Any]) -> list[str]:
    """Libraries installed here at a different minor version than wrote the release."""
    recorded = manifest.get("runtime", {})
    installed = runtime_versions()
    warnings = []
    for name in PICKLE_PACKAGES:
        if name not in recorded:
            continue
        if _minor(recorded[name]) != _minor(installed.get(name, "absent")):
            warnings.append(
                f"{name} {installed.get(name)} is installed but the release was written "
                f"by {recorded[name]}; its pickles may not load. `uv sync --locked` "
                "installs the versions the release was built with."
            )
    return warnings


# ---------------------------------------------------------------------------
# What is serving
# ---------------------------------------------------------------------------


def serving_version(artifacts_dir: Path) -> str | None:
    """The version an artifacts directory would serve, or None if nothing would.

    Read from the files rather than by loading the models: this runs in the
    container's entrypoint before the API starts, and needs to know only
    whether something is there. A directory holding nothing but the Phase 1
    preprocessing bundle serves nothing, so it does not count.
    """
    card_path = artifacts_dir / MODEL_CARD_FILE
    if not card_path.exists():
        return None
    has_stage1 = (artifacts_dir / SUPERVISED_FILE).exists()
    has_stage2 = (artifacts_dir / AUTOENCODER_FILE).exists()
    if not (has_stage1 or has_stage2):
        return None
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "an unreadable model card"
    stage2 = (card.get("stage2") or {}).get("version") if has_stage2 else None
    return compose_version(card.get("version") if has_stage1 else None, stage2)


def serving_an_untouched_release(artifacts_dir: Path) -> bool:
    """True when what is serving is an earlier release, byte for byte as installed.

    The install marker records the digests it wrote. If the serving files still
    match them, nobody has trained, promoted or copied anything over the release
    since, and replacing it with a newer one loses nothing.
    """
    marker = artifacts_dir / INSTALLED_MARKER
    if not marker.exists():
        return False
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    files = installed.get("files") or {}
    return set(files) == set(INSTALLED_FILES) and not check_files(artifacts_dir, files)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install(release_dir: Path, artifacts_dir: Path, *, force: bool = False) -> InstallResult:
    """Install the release into `artifacts_dir` unless something already serves there."""
    manifest = verify_release(release_dir)
    release_version = str(manifest["model_version"])
    current = serving_version(artifacts_dir)
    warnings = runtime_warnings(manifest)

    action = "installed"
    if current is not None and not force:
        installed_files = {name: manifest["files"][name] for name in INSTALLED_FILES}
        if current == release_version and not check_files(artifacts_dir, installed_files):
            return InstallResult("already-installed", release_version, current, warnings=warnings)
        if not serving_an_untouched_release(artifacts_dir):
            return InstallResult("kept", release_version, current, warnings=warnings)
        action = "upgraded"

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    staging = Path(tempfile.mkdtemp(prefix=".release-staging-", dir=artifacts_dir))
    replaced_into: Path | None = None
    try:
        for name in INSTALLED_FILES:
            shutil.copyfile(release_dir / name, staging / name)
        problems = check_files(staging, {name: manifest["files"][name] for name in INSTALLED_FILES})
        if problems:
            raise ReleaseError(
                "the staged copy does not match the manifest:\n  " + "\n  ".join(problems)
            )

        for name in INSTALLED_FILES:
            target = artifacts_dir / name
            # An upgrade replaces an earlier release exactly as installed, which
            # git already keeps; anything else displaced is kept beside it.
            displaced = target.exists() and action != "upgraded"
            if displaced and sha256_of(target) != manifest["files"][name]["sha256"]:
                if replaced_into is None:
                    replaced_into = artifacts_dir / f"replaced-{stamp}"
                    replaced_into.mkdir()
                shutil.move(str(target), replaced_into / name)
            (staging / name).replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    marker = {
        "installed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "from": str(release_dir),
        "model_version": release_version,
        "built_at": manifest.get("built_at"),
        "files": {name: manifest["files"][name] for name in INSTALLED_FILES},
    }
    (artifacts_dir / INSTALLED_MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return InstallResult(action, release_version, current, replaced_into, warnings)


# ---------------------------------------------------------------------------
# Build (maintainers: snapshot what is serving into the release)
# ---------------------------------------------------------------------------


def build(artifacts_dir: Path, release_dir: Path) -> dict[str, Any]:
    """Copy the serving model into `release_dir` and write its manifest.

    The bundle is loaded first, through the same loader the API uses, so a
    release cannot be cut from artifacts the service would refuse: a schema-hash
    mismatch raises here, and a half-trained directory (one stage only) is
    refused because a release that cannot fuse is not the system the README
    describes.
    """
    from app.inference import load_bundle

    bundle = load_bundle(artifacts_dir)
    if not (bundle.stage1_ready and bundle.stage2_ready):
        raise ReleaseError(
            f"{artifacts_dir} does not serve both stages (stage 1 ready: "
            f"{bundle.stage1_ready}, stage 2 ready: {bundle.stage2_ready}); a release "
            "ships the fused system or nothing."
        )
    missing = [name for name in INSTALLED_FILES if not (artifacts_dir / name).exists()]
    if missing:
        raise ReleaseError(f"{artifacts_dir} is missing {missing}; run the training pipeline.")

    release_dir.mkdir(parents=True, exist_ok=True)
    for name in INSTALLED_FILES:
        shutil.copyfile(artifacts_dir / name, release_dir / name)

    files = {name: _file_entry(release_dir / name) for name in INSTALLED_FILES}
    for name in DEMO_FILES:
        if (release_dir / name).exists():
            files[name] = _file_entry(release_dir / name)

    card = bundle.model_card
    manifest = {
        "format": MANIFEST_FORMAT,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "model_version": bundle.version,
        "stage1": {
            "version": bundle.stage1_version,
            "algorithm": bundle.supervised_algorithm,
            "classes": bundle.supervised_classes,
            "tau_sup": bundle.tau_sup,
            "trained_at": card.get("trained_at"),
            "trained_on": card.get("trained_on"),
        },
        "stage2": {
            "version": bundle.stage2_version,
            "algorithm": card.get("anomaly_algorithm"),
            "tau_anom": bundle.tau_anom,
            "trained_at": (card.get("stage2") or {}).get("trained_at"),
            "trained_on": (card.get("stage2") or {}).get("trained_on"),
        },
        "schema_hash": bundle.schema_hash,
        "feature_count": len(bundle.feature_order),
        "dataset": DATASET,
        "reproduce": REPRODUCE,
        "runtime": runtime_versions(),
        "files": files,
    }
    (release_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _status(release_dir: Path, artifacts_dir: Path) -> int:
    current = serving_version(artifacts_dir)
    print(f"artifacts  {artifacts_dir}")
    print(f"serving    {current or 'nothing'}")
    marker = artifacts_dir / INSTALLED_MARKER
    if current and marker.exists():
        installed = json.loads(marker.read_text(encoding="utf-8"))
        if installed.get("model_version") == current:
            print(f"           installed from the release at {installed.get('installed_at')}")
    try:
        manifest = verify_release(release_dir)
    except ReleaseError as exc:
        print(f"release    unusable: {exc}")
        return 1
    print(f"release    {manifest['model_version']}  (built {manifest.get('built_at')}, digests ok)")
    for warning in runtime_warnings(manifest):
        print(f"warning    {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from app.config import settings

    parser = argparse.ArgumentParser(
        prog="python -m app.release",
        description="Install, verify or build the committed model release.",
    )
    parser.add_argument("command", choices=("status", "install", "verify", "build"))
    parser.add_argument("--release-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="install even over a serving model; the displaced files are moved aside",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s | %(message)s")

    release_dir = args.release_dir or settings.release_path
    artifacts_dir = args.artifacts_dir or settings.artifacts_path

    try:
        if args.command == "status":
            return _status(release_dir, artifacts_dir)
        if args.command == "verify":
            manifest = verify_release(release_dir)
            print(f"release {manifest['model_version']}: {len(manifest['files'])} files match")
            for warning in runtime_warnings(manifest):
                print(f"warning: {warning}")
            return 0
        if args.command == "install":
            result = install(release_dir, artifacts_dir, force=args.force)
            print(result.describe())
            for warning in result.warnings:
                print(f"warning: {warning}")
            return 0
        manifest = build(artifacts_dir, release_dir)
        print(f"built release {manifest['model_version']} into {release_dir}")
        for name, entry in manifest["files"].items():
            print(f"  {name:28} {entry['bytes']:>12,} bytes  {entry['sha256'][:16]}")
        return 0
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
