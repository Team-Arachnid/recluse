"""The API contract snapshot: the OpenAPI schema, committed.

The dashboard's TypeScript types are generated from the OpenAPI schema and
committed (frontend/src/types/api.d.ts). Two things can then drift: the schema
can change without anyone regenerating the types, and the types can be edited
by hand. The snapshot written here sits between them. A backend test fails when
the live schema and the snapshot disagree, and a frontend test fails when the
types are not exactly what the snapshot generates -- so a change to the wire
format cannot land without the dashboard's types moving with it.

    python -m app.contract      # rewrite backend/tests/snapshots/openapi.json
    make openapi                # the same, then regenerate the frontend types
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR

SNAPSHOT_PATH: Path = BACKEND_DIR / "tests" / "snapshots" / "openapi.json"


def current_schema() -> dict[str, Any]:
    """The schema the application serves at /openapi.json, as plain JSON data."""
    from app.main import create_app

    # Through a JSON round trip so the comparison is between the same types the
    # snapshot file holds -- tuples become lists, and so on.
    return json.loads(json.dumps(create_app().openapi()))


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(render(current_schema()), encoding="utf-8")
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
