"""Phase 5 -- family to response playbook.

A static, reviewed lookup table, not a generative one. A fixed playbook is
something a SOC can trust; advice improvised per alert has to be re-verified
every time, which defeats the purpose of having it.

Unclassified anomalies get the honest entry: no playbook exists yet, route for
manual investigation. Never invent a fix for something the system does not
actually recognise -- a wrong playbook does more damage than an honest shrug.

Populated from the table in BUILD_PROMPT.md Part 8.
"""

from __future__ import annotations

from typing import Any


def playbook_for(*_: Any, **__: Any) -> dict[str, Any]:
    raise NotImplementedError("The remediation table is populated in Phase 5 (backend API).")
