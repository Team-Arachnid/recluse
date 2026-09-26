"""Console output for the training CLIs.

The reports these commands print are a deliverable of Phase 1, so they must
survive the terminal they are printed to. On Windows that terminal defaults to
cp1252, which cannot encode a great deal of legitimate text.
"""

from __future__ import annotations

import io

from training.console import echo


def test_text_outside_the_console_encoding_does_not_raise() -> None:
    """A cp1252 stdout must not take the process down mid-report."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    echo("rows: 2,572,640 \u2014 Web Attack \ufffd XSS", stream=stream)


def test_encodable_text_is_written_unchanged() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")

    echo("train (1,024,072 rows)", stream=stream)
    stream.flush()

    assert b"train (1,024,072 rows)" in buffer.getvalue()
