"""In-code tool handler registry + JSON-schema argument validation.

Only explicitly registered handlers can ever execute. Agents can never supply code,
SQL, or a handler of their own — a model-generated tool call is resolved to a
registered handler by name, or it is rejected.
"""

from __future__ import annotations

from typing import Any

from app.agents.tools.base import ToolHandler
from app.agents.tools.builtins import BUILTIN_TOOLS


class ToolRegistry:
    def __init__(self, handlers: list[ToolHandler] | None = None) -> None:
        self._by_identifier: dict[str, ToolHandler] = {}
        self._by_name: dict[str, ToolHandler] = {}
        for handler in handlers or BUILTIN_TOOLS:
            self.register(handler)

    def register(self, handler: ToolHandler) -> None:
        self._by_identifier[handler.handler_identifier] = handler
        self._by_name[handler.name] = handler

    def get_by_identifier(self, identifier: str) -> ToolHandler | None:
        return self._by_identifier.get(identifier)

    def get_by_name(self, name: str) -> ToolHandler | None:
        return self._by_name.get(name)

    def all(self) -> list[ToolHandler]:
        return list(self._by_identifier.values())


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Lightweight JSON-schema-ish validation (object/required/type/additionalProperties).

    Returns an error string when invalid, else None. Kept dependency-free and
    conservative — enough to reject malformed model tool calls safely.
    """
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        if key not in arguments:
            return f"missing required argument '{key}'"
    if schema.get("additionalProperties") is False:
        extra = set(arguments) - set(properties)
        if extra:
            return f"unexpected argument(s): {', '.join(sorted(extra))}"
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for key, value in arguments.items():
        spec = properties.get(key)
        if not spec:
            continue
        expected = spec.get("type")
        py_type = type_map.get(expected) if expected else None
        if py_type is not None and not isinstance(value, py_type):
            return f"argument '{key}' must be of type {expected}"
    return None


# Process-wide default registry (built-in tools).
tool_registry = ToolRegistry()
