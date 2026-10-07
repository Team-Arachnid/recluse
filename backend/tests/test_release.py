"""Phase 8 -- the committed model release and its installer.

`docker compose up` serving models on a clean clone rests on three properties,
and each is pinned here: the committed files are the ones the manifest
describes; an install produces a bundle the API loads (schema hash included);
and the installer never replaces a model somebody trained, never installs a file
whose digest is wrong, and upgrades only an untouched earlier release.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.config import settings
from app.inference import load_bundle
from app.release import (
    INSTALLED_FILES,
    INSTALLED_MARKER,
    MANIFEST_FILE,
    ReleaseError,
    install,
    read_manifest,
    runtime_warnings,
    serving_version,
    sha256_of,
    verify_release,
)


@pytest.fixture
def release_copy(tmp_path: Path) -> Path:
    """A private copy of the committed release that a test may damage."""
    copy = tmp_path / "release"
    shutil.copytree(settings.release_path, copy)
    return copy


def test_the_committed_release_matches_its_manifest() -> None:
    """A commit that changes a released file without rebuilding the manifest fails here."""
    manifest = verify_release(settings.release_path)

    assert set(INSTALLED_FILES) <= set(manifest["files"])
    assert manifest["schema_hash"].startswith("sha256:")
    assert manifest["stage1"]["version"] and manifest["stage2"]["version"]
    assert manifest["model_version"] == (
        f"{manifest['stage1']['version']}+{manifest['stage2']['version']}"
    )
    assert "Sharafaldin" in manifest["dataset"]["citation"]


def test_the_release_installs_into_an_empty_directory_and_serves_both_stages(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    manifest = read_manifest(settings.release_path)

    result = install(settings.release_path, artifacts)

    assert result.action == "installed"
    for name in INSTALLED_FILES:
        assert sha256_of(artifacts / name) == manifest["files"][name]["sha256"]
    # Loaded through the same loader the API uses: a release the service would
    # refuse (a schema-hash mismatch raises here) cannot pass this test.
    bundle = load_bundle(artifacts)
    assert bundle.stage1_ready and bundle.stage2_ready
    assert bundle.version == manifest["model_version"]
    assert bundle.schema_hash == manifest["schema_hash"]
    assert bundle.tau_anom == pytest.approx(manifest["stage2"]["tau_anom"])
    assert serving_version(artifacts) == manifest["model_version"]

    again = install(settings.release_path, artifacts)
    assert again.action == "already-installed"


def test_a_tampered_release_is_refused_and_installs_nothing(
    tmp_path: Path, release_copy: Path
) -> None:
    damaged = release_copy / "preprocessing.pkl"
    data = bytearray(damaged.read_bytes())
    data[len(data) // 2] ^= 0xFF
    damaged.write_bytes(bytes(data))
    artifacts = tmp_path / "artifacts"

    with pytest.raises(ReleaseError, match="preprocessing.pkl"):
        install(release_copy, artifacts)

    assert not artifacts.exists() or not any(artifacts.iterdir())


def test_a_missing_manifest_is_an_error_not_an_empty_release(
    tmp_path: Path, release_copy: Path
) -> None:
    (release_copy / MANIFEST_FILE).unlink()

    with pytest.raises(ReleaseError, match="MANIFEST"):
        install(release_copy, tmp_path / "artifacts")


def test_a_model_somebody_trained_is_never_replaced(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    install(settings.release_path, artifacts)
    # A local retrain: different weights, a different version on the card.
    card = json.loads((artifacts / "model_card.json").read_text())
    card["version"] = "stage1-lgbm-trained-here"
    (artifacts / "model_card.json").write_text(json.dumps(card))
    (artifacts / "supervised_model.pkl").write_bytes(b"a model trained on this machine")
    before = {name: sha256_of(artifacts / name) for name in INSTALLED_FILES}

    result = install(settings.release_path, artifacts)

    assert result.action == "kept"
    assert "stage1-lgbm-trained-here" in (result.serving_version or "")
    assert {name: sha256_of(artifacts / name) for name in INSTALLED_FILES} == before


def test_force_replaces_a_trained_model_and_keeps_what_it_displaced(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    install(settings.release_path, artifacts)
    (artifacts / "supervised_model.pkl").write_bytes(b"a model trained on this machine")

    result = install(settings.release_path, artifacts, force=True)

    assert result.action == "installed"
    assert result.replaced_into is not None
    assert (result.replaced_into / "supervised_model.pkl").read_bytes() == (
        b"a model trained on this machine"
    )
    manifest = read_manifest(settings.release_path)
    assert (
        sha256_of(artifacts / "supervised_model.pkl")
        == (manifest["files"]["supervised_model.pkl"]["sha256"])
    )


def test_an_untouched_earlier_release_is_upgraded(tmp_path: Path, release_copy: Path) -> None:
    """A `git pull` that brings a new release reaches a container's volume.

    Simulated by installing the release, then presenting a "newer" one: the same
    files with a rewritten card. What is serving is still the earlier release
    exactly as installed, so replacing it loses nothing.
    """
    artifacts = tmp_path / "artifacts"
    install(settings.release_path, artifacts)

    card_path = release_copy / "model_card.json"
    card = json.loads(card_path.read_text())
    card["version"] = "stage1-lgbm-next-release"
    card_path.write_text(json.dumps(card))
    manifest = json.loads((release_copy / MANIFEST_FILE).read_text())
    manifest["model_version"] = "stage1-lgbm-next-release+" + manifest["stage2"]["version"]
    manifest["files"]["model_card.json"] = {
        "sha256": sha256_of(card_path),
        "bytes": card_path.stat().st_size,
    }
    (release_copy / MANIFEST_FILE).write_text(json.dumps(manifest))

    result = install(release_copy, artifacts)

    assert result.action == "upgraded"
    assert serving_version(artifacts) == manifest["model_version"]
    marker = json.loads((artifacts / INSTALLED_MARKER).read_text())
    assert marker["model_version"] == manifest["model_version"]


def test_a_library_version_mismatch_is_reported(release_copy: Path) -> None:
    manifest = read_manifest(release_copy)
    manifest["runtime"]["lightgbm"] = "1.0.0"

    warnings = runtime_warnings(manifest)

    assert any("lightgbm" in warning for warning in warnings)
    assert runtime_warnings(read_manifest(settings.release_path)) == []
