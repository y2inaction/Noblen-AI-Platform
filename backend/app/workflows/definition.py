"""Workflow definitions: a trigger and a list of typed steps (validated, versioned).

```json
{
  "trigger": {"type": "schedule", "daily_at": "08:00"},
  "steps": [
    {"id": "open", "type": "tool", "tool": "list_tasks", "arguments": {"open_only": true}},
    {"id": "any", "type": "condition", "left": "{{ steps.open.output.total }}",
     "op": "gt", "right": 0, "then": "brief", "else": "end"},
    {"id": "brief", "type": "agent", "agent_id": "…",
     "input": "Write a morning briefing from: {{ steps.open.output.tasks }}"},
    {"id": "ok", "type": "approval", "title": "Send the briefing?"},
    {"id": "send", "type": "tool", "tool": "notify_member",
     "arguments": {"recipient_email": "{{ input.email }}", "title": "Briefing",
                   "body": "{{ steps.brief.output.text }}"}}
  ]
}
```

Steps run in list order unless `next` (or a condition's `then` / `else`, or an
approval's `on_reject`) names another step id, or `"end"`.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.workflows.templating import check_references

END = "end"
EVENTS = ("task.created", "task.completed")
_STEP_ID = r"^[a-z][a-z0-9_]{0,47}$"
_DAILY = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Trigger(_Model):
    type: Literal["manual", "schedule", "event"] = "manual"
    every_minutes: int | None = Field(default=None, le=10_080)
    daily_at: str | None = None  # "HH:MM" in the organization's timezone
    event: Literal["task.created", "task.completed"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Trigger:
        if self.type == "schedule":
            if (self.every_minutes is None) == (self.daily_at is None):
                raise ValueError("A schedule needs exactly one of every_minutes or daily_at.")
            if (
                self.every_minutes is not None
                and self.every_minutes < settings.WORKFLOW_MIN_INTERVAL_MINUTES
            ):
                raise ValueError(
                    f"every_minutes must be at least {settings.WORKFLOW_MIN_INTERVAL_MINUTES}."
                )
            if self.daily_at is not None and not _DAILY.match(self.daily_at):
                raise ValueError("daily_at must be HH:MM (24-hour).")
        elif self.every_minutes is not None or self.daily_at is not None:
            raise ValueError("every_minutes/daily_at are only for schedule triggers.")
        if (self.type == "event") != (self.event is not None):
            raise ValueError("Event triggers (and only they) name an event.")
        return self


class RetryPolicy(_Model):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: int = Field(default=30, ge=0, le=3600)


class _Step(_Model):
    id: str = Field(pattern=_STEP_ID)
    name: str | None = Field(default=None, max_length=160)
    next: str | None = None
    on_failure: Literal["fail", "continue", "escalate"] = "fail"
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    def targets(self) -> list[str]:
        return [t for t in (self.next,) if t]


class AgentStep(_Step):
    type: Literal["agent"]
    agent_id: uuid.UUID
    input: str = Field(min_length=1, max_length=20_000)


class ToolStep(_Step):
    type: Literal["tool"]
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Pause for a human decision before the tool runs (forced for HIGH-risk tools).
    require_approval: bool = False


ConditionOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "empty"]


class ConditionStep(_Step):
    type: Literal["condition"]
    left: Any = None
    op: ConditionOp
    right: Any = None
    then: str
    else_: str = Field(alias="else")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def targets(self) -> list[str]:
        return [self.then, self.else_, *super().targets()]


class ApprovalStep(_Step):
    type: Literal["approval"]
    title: str = Field(min_length=1, max_length=255)
    details: str | None = Field(default=None, max_length=10_000)
    # Where to go when the reviewer rejects; without it the run is cancelled.
    on_reject: str | None = None

    def targets(self) -> list[str]:
        return [t for t in (self.on_reject,) if t] + super().targets()


Step = Annotated[AgentStep | ToolStep | ConditionStep | ApprovalStep, Field(discriminator="type")]


class WorkflowDefinition(_Model):
    trigger: Trigger = Field(default_factory=Trigger)
    steps: list[Step] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def _bounded(cls, steps: list[Any]) -> list[Any]:
        if len(steps) > settings.WORKFLOW_MAX_STEPS:
            raise ValueError(f"A workflow has at most {settings.WORKFLOW_MAX_STEPS} steps.")
        return steps

    @model_validator(mode="after")
    def _graph(self) -> WorkflowDefinition:
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("Step ids must be unique.")
        if END in ids:
            raise ValueError("'end' is reserved and cannot be a step id.")
        known = set(ids) | {END}
        for step in self.steps:
            for target in step.targets():
                if target not in known:
                    raise ValueError(f"Step '{step.id}' points to unknown step '{target}'.")
            for field in ("input", "arguments", "left", "right", "title", "details"):
                if hasattr(step, field):
                    check_references(getattr(step, field))
        return self

    def step(self, step_id: str) -> Any:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def following(self, step_id: str) -> str | None:
        """The step after `step_id` in list order (None at the end)."""
        ids = [s.id for s in self.steps]
        index = ids.index(step_id)
        return ids[index + 1] if index + 1 < len(ids) else None

    def next_of(self, step: Any) -> str | None:
        target = step.next or self.following(step.id)
        return None if target in (None, END) else target


def parse(definition: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(definition)
