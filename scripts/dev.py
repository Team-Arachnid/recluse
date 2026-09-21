#!/usr/bin/env python3
"""Run the backend and frontend together, natively.

`make dev` calls this. It exists instead of a shell one-liner because
backgrounding two processes and cleaning both up on Ctrl-C is not portable
between GNU make on Linux, Git Bash on Windows, and PowerShell.

Ports come from .env, so the URLs printed here are the ones actually served.

For the containerised stack use `make up` (docker compose) instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

RESET = "\033[0m"
COLOURS = {"backend": "\033[36m", "frontend": "\033[35m", "setup": "\033[33m"}


def log(source: str, message: str) -> None:
    colour = COLOURS.get(source, "")
    print(f"{colour}[{source}]{RESET} {message}", flush=True)


def read_env() -> dict[str, str]:
    """Parse .env well enough to find the ports, ignoring inline comments."""
    values: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return values
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split("#", 1)[0].strip().strip("\"'")
    return values


def ensure_env_file() -> None:
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        return
    template = REPO_ROOT / ".env.example"
    shutil.copyfile(template, env_file)
    log("setup", f"created {env_file.name} from {template.name}")


def require(tool: str, hint: str) -> None:
    if shutil.which(tool) is None:
        log("setup", f"{tool!r} not found on PATH. {hint}")
        raise SystemExit(1)


def ensure_installed() -> None:
    if not (BACKEND_DIR / ".venv").exists():
        log("setup", "backend venv missing -- running uv sync")
        subprocess.run(["uv", "sync"], cwd=BACKEND_DIR, check=True)
    if not (FRONTEND_DIR / "node_modules").exists():
        log("setup", "frontend node_modules missing -- running npm install")
        subprocess.run(["npm", "install", "--no-fund"], cwd=FRONTEND_DIR, check=True)


def migrate() -> None:
    log("setup", "applying database migrations")
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True)


def pump(source: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        log(source, line.rstrip())


def main() -> int:
    require("uv", "Install from https://docs.astral.sh/uv/")
    require("npm", "Install Node.js 20.19+ or 22.12+")

    ensure_env_file()
    ensure_installed()
    migrate()

    env_values = read_env()
    backend_port = env_values.get("IDS_PORT", "8000")
    frontend_port = env_values.get("VITE_DEV_SERVER_PORT", "5173")
    backend_host = env_values.get("IDS_HOST", "127.0.0.1")

    child_env = {**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"}

    commands: dict[str, tuple[list[str], Path]] = {
        "backend": (
            [
                "uv",
                "run",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                backend_host,
                "--port",
                backend_port,
            ],
            BACKEND_DIR,
        ),
        "frontend": (["npm", "run", "dev"], FRONTEND_DIR),
    }

    processes: dict[str, subprocess.Popen[str]] = {}
    threads: list[threading.Thread] = []

    # Put children in their own process group. On Windows, uvicorn --reload
    # restarting its worker raises a console control event that otherwise
    # propagates here and takes the whole launcher down -- editing one backend
    # file would kill the frontend too. Ctrl-C still works: the finally block
    # terminates both children explicitly.
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
    )

    try:
        for source, (command, cwd) in commands.items():
            log(source, "starting: " + " ".join(command))
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=os.name == "nt",  # npm is a .cmd shim on Windows
                creationflags=creation_flags,
            )
            processes[source] = process
            thread = threading.Thread(target=pump, args=(source, process), daemon=True)
            thread.start()
            threads.append(thread)

        log("setup", f"API      http://localhost:{backend_port}/api/v1/health")
        log("setup", f"API docs http://localhost:{backend_port}/docs")
        log("setup", f"Dashboard http://localhost:{frontend_port}")
        log("setup", "Ctrl-C to stop both")

        # Exit as soon as either side dies, so a crashed backend is not hidden
        # behind a still-running frontend.
        while True:
            for source, process in processes.items():
                code = process.poll()
                if code is not None:
                    log("setup", f"{source} exited with code {code} -- shutting down")
                    return code or 1
            for thread in threads:
                thread.join(timeout=0.2)
    except KeyboardInterrupt:
        log("setup", "stopping")
        return 0
    finally:
        for source, process in processes.items():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log("setup", f"{source} did not stop -- killing")
                    process.kill()


if __name__ == "__main__":
    sys.exit(main())
