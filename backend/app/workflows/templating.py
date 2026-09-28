"""Safe value references for workflow steps: `{{ steps.lookup.output.total }}`.

This is deliberately not a template language. A placeholder is a dotted path
into the run context (`input`, `trigger`, `steps.<id>.output`, `run`) and
nothing else: no expressions, filters, attribute access or code execution. A
string that is exactly one placeholder keeps the referenced value's type;
otherwise values are interpolated as text (JSON for objects and lists).
"""

from __future__ import annotations

import json
import re
from typing import Any

_REF = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_ROOTS = {"input", "trigger", "steps", "run"}
MAX_RENDERED_CHARS = 20_000


class TemplateError(ValueError):
    pass


def references(value: Any) -> list[str]:
    """Every placeholder path in a value (recursively)."""
    if isinstance(value, str):
        return _REF.findall(value)
    if isinstance(value, dict):
        return [r for v in value.values() for r in references(v)]
    if isinstance(value, list):
        return [r for v in value for r in references(v)]
    return []


def check_references(value: Any) -> None:
    for ref in references(value):
        if ref.split(".", 1)[0] not in _ROOTS:
            raise TemplateError(
                f"Unknown reference '{{{{ {ref} }}}}'. Use input., trigger., steps. or run."
            )


def lookup(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise TemplateError(f"'{{{{ {path} }}}}' has no value in this run.")
    return current


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)
    return str(value)


def render(value: Any, context: dict[str, Any]) -> Any:
    """Resolve placeholders in strings, dicts and lists."""
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    if not isinstance(value, str):
        return value
    whole = _REF.fullmatch(value.strip())
    if whole:
        return lookup(context, whole.group(1))
    rendered = _REF.sub(lambda m: _as_text(lookup(context, m.group(1))), value)
    if len(rendered) > MAX_RENDERED_CHARS:
        raise TemplateError("A rendered value is too long.")
    return rendered
