#!/usr/bin/env python3
"""Mirror ``wiki/`` into the repository's GitHub wiki.

The wiki pages are authored in this repository, under ``wiki/``, so that a
documentation change is reviewed in the same pull request as the code change
that caused it. GitHub serves a wiki from a *second*, separate git repository
(``<repo>.wiki.git``), which nothing in a normal push touches. This script is
the bridge: it clones that second repository, makes its contents identical to
``wiki/``, and pushes only when something actually differs.

It is idempotent. Running it twice in a row does nothing the second time, so it
is safe to wire into a ``post-commit`` hook or a CI job.

Usage::

    python scripts/publish_wiki.py                 # publish if there are changes
    python scripts/publish_wiki.py --dry-run       # report what would change
    python scripts/publish_wiki.py -m "message"    # override the commit message
    python scripts/publish_wiki.py --check         # exit 1 if out of sync, push nothing

Authentication uses whatever git is already configured with. In CI, set
``GITHUB_TOKEN`` (or ``GH_TOKEN``) and the token is injected into the clone URL.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "wiki"

# Only these land in the wiki. Everything else in wiki/ is ignored, which keeps
# the door open for local scratch files without them being published.
PUBLISHED_SUFFIXES = (".md", ".png", ".jpg", ".jpeg", ".svg", ".gif")

RESET = "\033[0m"
COLOURS = {"wiki": "\033[36m", "git": "\033[35m", "warn": "\033[33m", "error": "\033[31m"}


def log(source: str, message: str) -> None:
    colour = COLOURS.get(source, "")
    print(f"{colour}[{source}]{RESET} {message}", flush=True)


def run(
    args: list[str],
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, returning the completed process."""
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        capture_output=capture,
    )


def git_output(args: list[str], cwd: Path | None = None, default: str = "") -> str:
    try:
        return run(["git", *args], cwd=cwd).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return default


def wiki_remote_url(explicit: str | None = None) -> str:
    """Work out which wiki repository to push to.

    Precedence: an explicit ``--remote``, then ``WIKI_REMOTE_URL``, then the
    ``origin`` remote of this repository with ``.wiki.git`` substituted for
    ``.git``. SSH remotes are kept as SSH so an existing key still works.
    """
    if explicit:
        return explicit

    env_url = os.environ.get("WIKI_REMOTE_URL")
    if env_url:
        return env_url

    origin = git_output(["remote", "get-url", "origin"], cwd=REPO_ROOT)
    if not origin:
        raise SystemExit(
            "No 'origin' remote and no --remote given. Pass --remote "
            "https://github.com/<owner>/<repo>.wiki.git"
        )

    if origin.endswith(".wiki.git"):
        return origin
    if origin.endswith(".git"):
        return origin[: -len(".git")] + ".wiki.git"
    return origin.rstrip("/") + ".wiki.git"


def authenticated_url(url: str) -> str:
    """Inject a CI token into an HTTPS URL, if one is present in the env.

    Interactive runs on a developer machine hit neither branch: git's own
    credential helper handles those, and no token is written anywhere.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not url.startswith("https://"):
        return url
    if "@" in url.split("//", 1)[1].split("/", 1)[0]:
        return url
    return url.replace("https://", f"https://x-access-token:{token}@", 1)


def redact(url: str) -> str:
    """Hide credentials before printing a URL."""
    return re.sub(r"//[^/@]+@", "//***@", url)


def display_path(path: Path) -> str:
    """Render a path relative to the repository when it lives inside it.

    ``--source`` may legitimately point somewhere else entirely, so the
    relative form is an optimisation for readability, not an assumption.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def published_files(root: Path) -> dict[str, Path]:
    """Map publish-relative path -> source path for everything we publish."""
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in PUBLISHED_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        found[path.relative_to(root).as_posix()] = path
    return found


def existing_files(root: Path) -> dict[str, Path]:
    """Everything currently in the wiki checkout, ignoring its .git directory."""
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(root).parts:
            continue
        found[path.relative_to(root).as_posix()] = path
    return found


def sync(source: Path, target: Path) -> tuple[list[str], list[str], list[str]]:
    """Make ``target`` contain exactly ``source``.

    Returns (added, updated, removed) as lists of publish-relative paths.
    """
    wanted = published_files(source)
    present = existing_files(target)

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []

    for name, src in wanted.items():
        dst = target / name
        if name not in present:
            added.append(name)
        elif not filecmp.cmp(src, dst, shallow=False):
            updated.append(name)
        else:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for name in present:
        if name not in wanted:
            removed.append(name)
            (target / name).unlink()

    return added, updated, removed


def default_message() -> str:
    """Tie the wiki commit back to the source commit that produced it."""
    sha = git_output(["rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, default="")
    subject = git_output(["log", "-1", "--pretty=%s"], cwd=REPO_ROOT, default="")
    if sha and subject:
        return f"Update wiki from {sha} — {subject}"
    if sha:
        return f"Update wiki from {sha}"
    return "Update wiki"


def committer_identity() -> tuple[str, str]:
    """Reuse this repository's git identity for the wiki commit."""
    name = (
        os.environ.get("WIKI_GIT_NAME")
        or git_output(["config", "user.name"], cwd=REPO_ROOT)
        or "recluse-docs"
    )
    email = (
        os.environ.get("WIKI_GIT_EMAIL")
        or git_output(["config", "user.email"], cwd=REPO_ROOT)
        or "recluse-docs@users.noreply.github.com"
    )
    return name, email


def report(added: list[str], updated: list[str], removed: list[str]) -> None:
    for name in added:
        log("wiki", f"  + {name}")
    for name in updated:
        log("wiki", f"  ~ {name}")
    for name in removed:
        log("wiki", f"  - {name}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="publish_wiki.py",
        description="Mirror wiki/ into the repository's GitHub wiki.",
    )
    parser.add_argument("-m", "--message", help="Commit message for the wiki commit")
    parser.add_argument("--remote", help="Wiki remote URL (default: origin with .wiki.git)")
    parser.add_argument(
        "--source",
        default=str(SOURCE_DIR),
        help="Directory holding the wiki pages (default: wiki/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without cloning a writable checkout or pushing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the wiki is out of date. Implies --dry-run. For CI.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dry_run = args.dry_run or args.check

    source = Path(args.source).resolve()
    if not source.is_dir():
        log("error", f"No wiki source directory at {source}")
        return 1

    pages = published_files(source)
    if not pages:
        log("error", f"{source} contains no publishable files")
        return 1

    if shutil.which("git") is None:
        log("error", "git is not on PATH")
        return 1

    remote = wiki_remote_url(args.remote)
    log("wiki", f"{len(pages)} page(s) in {display_path(source)}")
    log("wiki", f"target {redact(remote)}")

    with tempfile.TemporaryDirectory(prefix="recluse-wiki-") as tmp:
        checkout = Path(tmp) / "wiki"
        clone = run(
            [
                "git",
                # The wiki repository carries no .gitattributes, so on Windows
                # core.autocrlf would check every page out with CRLF while the
                # source in wiki/ is LF. Every file would then compare as
                # changed on every run, and --check would never go green.
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "clone",
                "--depth",
                "1",
                "--quiet",
                authenticated_url(remote),
                str(checkout),
            ],
            check=False,
        )
        if clone.returncode != 0:
            stderr = clone.stderr.strip()
            if "not found" in stderr.lower() or "does not exist" in stderr.lower():
                web = redact(remote).replace(".wiki.git", "/wiki")
                log("error", "The GitHub wiki for this repository does not exist yet.")
                log("error", "GitHub does not create the wiki repository until it has one page,")
                log("error", "and that first page can only be created through the web UI:")
                log("error", "  1. Settings -> General -> Features -> tick 'Wikis'")
                log("error", f"  2. Open {web}/_new and save a page called Home")
                log("error", "     (the content does not matter; step 3 overwrites it)")
                log("error", "  3. Re-run this script")
                log("error", "This is a one-time step per repository.")
            else:
                log("error", f"git clone failed: {stderr}")
            return 1

        added, updated, removed = sync(source, checkout)

        if not (added or updated or removed):
            log("wiki", "already up to date, nothing to push")
            return 0

        log("wiki", f"{len(added)} added, {len(updated)} updated, {len(removed)} removed")
        report(added, updated, removed)

        if dry_run:
            if args.check:
                log("warn", "wiki is out of date (--check)")
                return 1
            log("wiki", "dry run, nothing pushed")
            return 0

        name, email = committer_identity()
        run(["git", "config", "core.autocrlf", "false"], cwd=checkout)
        run(["git", "config", "core.eol", "lf"], cwd=checkout)
        run(["git", "config", "user.name", name], cwd=checkout)
        run(["git", "config", "user.email", email], cwd=checkout)
        run(["git", "add", "--all"], cwd=checkout)

        message = args.message or default_message()
        commit = run(["git", "commit", "-m", message], cwd=checkout, check=False)
        if commit.returncode != 0:
            # Content differed only in mode or line endings; git found nothing.
            log("wiki", "git found nothing to commit after normalisation")
            return 0

        push = run(["git", "push", "origin", "HEAD"], cwd=checkout, check=False)
        if push.returncode != 0:
            log("error", f"git push failed: {push.stderr.strip()}")
            return 1

    log("wiki", f"published: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
