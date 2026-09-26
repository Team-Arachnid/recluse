"""Console output for the training CLIs.

The reports these commands print -- cleaning counts, row counts per split per
class -- are deliverables of Phase 1, not decoration. They have to survive the
terminal they land in.

On Windows that terminal defaults to cp1252, which cannot encode most of what
a dataset can put in a class name. The published CICIDS2017 labels already
carry a cp1252 en dash, and the widely-mirrored UTF-8 conversion of them
carries U+FFFD in its place. Printing either one through a cp1252 stdout raises
UnicodeEncodeError and kills the run *after* the real work is finished, which
is the worst possible moment.

Labels are normalised on the way in, so in practice nothing unencodable should
reach here. This is the belt to that braces: a report is worth degrading a
character for, never worth crashing over.
"""

from __future__ import annotations

import sys
from typing import TextIO


def echo(text: str, stream: TextIO | None = None) -> None:
    """Write a line to ``stream`` (default stdout), degrading rather than raising.

    Characters the stream cannot encode are replaced. The alternative --
    letting the exception propagate -- loses the entire report over one byte.
    """
    stream = stream if stream is not None else sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        stream.write(safe + "\n")
