"""Structured JSON logging with request-scoped context.

Every log line can carry request_id, organization_id, user_id, agent_id and
workflow_id so events are traceable across the platform (see ARCHITECTURE.md §12).
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

# Request-scoped context, populated by middleware / services.
# Default is None (not a mutable {}) to avoid a shared-mutable-default footgun.
_request_context: ContextVar[dict[str, Any] | None] = ContextVar("request_context", default=None)


def _current() -> dict[str, Any]:
    return _request_context.get() or {}


def bind_context(**kwargs: Any) -> None:
    """Merge key/values into the current request's logging context."""
    current = dict(_current())
    current.update({k: v for k, v in kwargs.items() if v is not None})
    _request_context.set(current)


def clear_context() -> None:
    _request_context.set({})


def _inject_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in _current().items():
        event_dict.setdefault(key, value)
    return event_dict


def configure_logging(debug: bool = False) -> None:
    """Configure structlog to emit structured logs (JSON in prod, console in dev)."""
    renderer: Any = (
        structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
