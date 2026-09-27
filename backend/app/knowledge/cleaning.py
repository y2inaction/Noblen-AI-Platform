"""Deterministic text cleaning.

Removes control-character noise and collapses excessive whitespace/blank lines while
preserving paragraph and list structure. It never aggressively rewrites the source —
the knowledge base must faithfully represent the document.
"""

from __future__ import annotations

import re

# Control chars except tab/newline/carriage-return.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRAILING_WS = re.compile(r"[ \t]+(\n)")
_MANY_SPACES = re.compile(r"[ \t]{2,}")
_MANY_BLANKLINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _TRAILING_WS.sub(r"\1", text)  # strip trailing spaces on each line
    text = _MANY_SPACES.sub(" ", text)  # collapse runs of spaces/tabs
    text = _MANY_BLANKLINES.sub("\n\n", text)  # collapse 3+ blank lines to one
    return text.strip()
