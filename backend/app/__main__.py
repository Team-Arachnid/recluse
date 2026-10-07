"""Run the API with uvicorn on the configured host and port.

``python -m app`` is what ``make backend`` / ``make.ps1 backend`` invoke, so
running the API on its own binds the same ``IDS_HOST`` / ``IDS_PORT`` that the
full-stack launcher (``scripts/dev.py``) reads from ``.env``. Previously the
backend target hardcoded uvicorn's own defaults (127.0.0.1:8000) and ignored
the environment, so moving the API off a busy or OS-reserved port in ``.env``
fixed ``make dev`` but not ``make backend`` -- the two disagreed on where the
API listened.
"""

from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
