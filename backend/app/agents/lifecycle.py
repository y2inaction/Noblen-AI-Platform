"""Agent lifecycle state machine (server-enforced transitions)."""

from __future__ import annotations

from app.agents.errors import InvalidAgentStateTransition
from app.models.enums import AgentStatus

# Allowed transitions between agent statuses.
_TRANSITIONS: dict[str, set[str]] = {
    AgentStatus.DRAFT.value: {AgentStatus.TESTING.value, AgentStatus.ARCHIVED.value},
    AgentStatus.TESTING.value: {
        AgentStatus.ACTIVE.value,
        AgentStatus.DRAFT.value,
        AgentStatus.ARCHIVED.value,
    },
    AgentStatus.ACTIVE.value: {AgentStatus.PAUSED.value, AgentStatus.ARCHIVED.value},
    AgentStatus.PAUSED.value: {AgentStatus.ACTIVE.value, AgentStatus.ARCHIVED.value},
    AgentStatus.ARCHIVED.value: set(),  # terminal
}


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, set())


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidAgentStateTransition(
            f"Cannot transition agent from '{current}' to '{target}'."
        )
