"""Phase 5 -- attack family to MITRE ATT&CK technique mapping.

A static, reviewed lookup. Each entry carries the technique ID plus a one-line
plain-English description, so the Alert Detail screen can answer what this
likely is without the analyst opening a second tab.

UNCLASSIFIED_ANOMALY maps to no technique, on purpose. Saying so plainly is
the honest answer and it is the whole point of Stage 2.

Populated from the table in BUILD_PROMPT.md Part 8, which also backs the
MITRE coverage heatmap on the analytics screen.
"""

from __future__ import annotations

from typing import Any


def technique_for(*_: Any, **__: Any) -> dict[str, Any] | None:
    raise NotImplementedError("The MITRE lookup is populated in Phase 5 (backend API).")
