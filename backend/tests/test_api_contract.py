"""Phase 8 -- the API contract cannot change by accident.

The live OpenAPI schema must equal the committed snapshot
(backend/tests/snapshots/openapi.json). The frontend's own contract test
(frontend/src/types/contract.test.ts) then requires the committed TypeScript
types to be exactly what that snapshot generates. Changing the wire format is
allowed; doing it without updating both is what fails:

    make openapi
"""

from __future__ import annotations

import difflib
import json

import pytest

from app.contract import SNAPSHOT_PATH, current_schema, render


def test_the_served_schema_matches_the_committed_snapshot() -> None:
    assert SNAPSHOT_PATH.exists(), f"{SNAPSHOT_PATH} is missing; run `make openapi`."
    committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    current = current_schema()

    if current != committed:
        diff = "".join(
            list(
                difflib.unified_diff(
                    render(committed).splitlines(keepends=True),
                    render(current).splitlines(keepends=True),
                    fromfile="committed snapshot",
                    tofile="served schema",
                )
            )[:60]
        )
        pytest.fail(
            "The API contract changed without its snapshot. If the change is "
            "intended, run `make openapi` to rewrite the snapshot and regenerate "
            f"the frontend types, and commit both.\n\n{diff}"
        )


def test_every_operation_declares_a_response_schema() -> None:
    """A route with no response model hands the frontend an untyped `unknown`.

    The SSE stream is the one exception, and it is documented as such: its body
    is a stream of events, typed by AlertEvent and HeartbeatEvent instead.
    """
    untyped = []
    for path, operations in current_schema()["paths"].items():
        for method, operation in operations.items():
            if path.endswith("/stream"):
                continue
            success = {
                code: body for code, body in operation["responses"].items() if code.startswith("2")
            }
            if not any(
                "schema" in media
                for body in success.values()
                for media in body.get("content", {}).values()
            ):
                untyped.append(f"{method.upper()} {path}")

    assert untyped == []
