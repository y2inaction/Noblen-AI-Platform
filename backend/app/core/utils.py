"""Small shared utilities."""

from __future__ import annotations

import re
import secrets

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _slug_re.sub("-", value.strip().lower()).strip("-")
    return slug or "org"


def unique_suffix(nchars: int = 6) -> str:
    return secrets.token_hex(nchars)[:nchars]
