#!/usr/bin/env python3
"""Install this repository's git hooks into ``.git/hooks``.

Hooks are not version-controlled by git itself, so they are kept in
``scripts/hooks/`` and copied into place on demand. Copying rather than
symlinking keeps the behaviour identical on Windows, where symlink creation
needs either developer mode or elevation.

Usage::

    python scripts/install_hooks.py             # install
    python scripts/install_hooks.py --list      # show what is installed
    python scripts/install_hooks.py --uninstall # remove the hooks it installed
    python scripts/install_hooks.py --force     # overwrite an unrelated hook
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SOURCE = REPO_ROOT / "scripts" / "hooks"

# Written into every installed hook so --uninstall can tell ours apart from a
# hook someone added by hand.
MARKER = "# Installed by scripts/install_hooks.py — do not edit here; edit scripts/hooks/."

RESET = "\033[0m"
COLOURS = {"hooks": "\033[36m", "warn": "\033[33m", "error": "\033[31m"}


def log(source: str, message: str) -> None:
    colour = COLOURS.get(source, "")
    print(f"{colour}[{source}]{RESET} {message}", flush=True)


def hooks_dir() -> Path:
    """Resolve .git/hooks, honouring core.hooksPath and git worktrees."""
    try:
        configured = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
        if configured:
            path = Path(configured)
            return path if path.is_absolute() else REPO_ROOT / path

        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit("Not a git repository, or git is not on PATH") from exc

    git_dir = Path(common)
    if not git_dir.is_absolute():
        git_dir = REPO_ROOT / git_dir
    return git_dir / "hooks"


def available_hooks() -> list[Path]:
    if not HOOK_SOURCE.is_dir():
        return []
    return sorted(p for p in HOOK_SOURCE.iterdir() if p.is_file() and not p.name.startswith("."))


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install(target_dir: Path, force: bool) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = 0

    for source in available_hooks():
        target = target_dir / source.name
        if target.exists() and MARKER not in target.read_text(encoding="utf-8", errors="ignore"):
            if not force:
                log("warn", f"{target.name}: an unrelated hook is already installed, skipping")
                log("warn", "  re-run with --force to overwrite it")
                continue
            log("warn", f"{target.name}: overwriting an unrelated hook (--force)")

        body = source.read_text(encoding="utf-8")
        lines = body.splitlines()
        if lines and lines[0].startswith("#!"):
            body = "\n".join([lines[0], MARKER, *lines[1:]]) + "\n"
        else:
            body = MARKER + "\n" + body

        target.write_text(body, encoding="utf-8", newline="\n")
        make_executable(target)
        log("hooks", f"installed {target.name}")
        installed += 1

    if installed:
        log("hooks", f"{installed} hook(s) installed into {target_dir}")
    else:
        log("hooks", "nothing installed")
    return 0


def uninstall(target_dir: Path) -> int:
    removed = 0
    for source in available_hooks():
        target = target_dir / source.name
        if not target.exists():
            continue
        if MARKER not in target.read_text(encoding="utf-8", errors="ignore"):
            log("warn", f"{target.name}: not installed by this script, left alone")
            continue
        target.unlink()
        log("hooks", f"removed {target.name}")
        removed += 1
    log("hooks", f"{removed} hook(s) removed")
    return 0


def show(target_dir: Path) -> int:
    log("hooks", f"hooks directory: {target_dir}")
    for source in available_hooks():
        target = target_dir / source.name
        if not target.exists():
            state = "not installed"
        elif MARKER in target.read_text(encoding="utf-8", errors="ignore"):
            state = "installed"
        else:
            state = "present, but not ours"
        log("hooks", f"  {source.name:<16} {state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_hooks.py",
        description="Install the repository's git hooks from scripts/hooks/.",
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove the installed hooks")
    parser.add_argument("--list", action="store_true", help="Show hook status and exit")
    parser.add_argument("--force", action="store_true", help="Overwrite hooks we did not install")
    args = parser.parse_args(argv)

    if shutil.which("git") is None:
        log("error", "git is not on PATH")
        return 1

    target_dir = hooks_dir()

    if args.list:
        return show(target_dir)
    if args.uninstall:
        return uninstall(target_dir)
    return install(target_dir, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
