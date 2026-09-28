"""Who may see the *content* of run-like resources (ADR-0035, ADR-0036).

A run acts for exactly one person (the starter of a manual run, the activator of a
triggered workflow) and reads with that person's authority: their private memories,
knowledge restricted to them, their integrations. What a run was given, retrieved
and produced is therefore participant data (ADR-0036 level P). It is visible to that
person only. Roles never widen it; viewers, members, managers and admins are treated
alike, because permissions gate actions and resource types, not rows.

Everything else about a run is metadata and stays organization-visible under the
resource's `:view` permission: ids, status, timings, counts, cost, error codes, and
who initiated it.

One exception keeps governance working. Holders of `agent:approve_actions` see the
approval requests they are asked to decide, and nothing else of the run.

The rule is applied here, once per resource, by the presenters the endpoints use.
Endpoints never pick fields themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.models.enums import RunStepStatus, RunStepType, WorkflowStepStatus
from app.models.run import AgentRun, AgentRunStep
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.rbac.permissions import Permission, role_has_permission
from app.schemas.run import RunDetailOut, RunOut, RunStepOut
from app.schemas.workflow import WorkflowRunDetail, WorkflowRunOut, WorkflowStepRunOut


@dataclass(frozen=True)
class Viewer:
    """The person a response is for, in the active organization."""

    user_id: uuid.UUID
    role_name: str
    is_superuser: bool = False

    def may(self, permission: str) -> bool:
        return self.is_superuser or role_has_permission(self.role_name, permission)


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def sees_run_content(viewer: Viewer, acting_user_id: uuid.UUID | None) -> bool:
    """Participant rule: only the person a run acts for sees its content."""
    return acting_user_id is not None and acting_user_id == viewer.user_id


def sees_approval_requests(viewer: Viewer) -> bool:
    """Approvers see the requests they decide (their rendered title and details)."""
    return viewer.may(Permission.AGENT_APPROVE_ACTIONS)


# Content fields per resource. Every field not listed is metadata.
WORKFLOW_RUN_CONTENT = ("input", "error")  # plus `context` on the detail view
WORKFLOW_STEP_CONTENT = ("output", "error", "decision_note")
AGENT_RUN_CONTENT = ("escalation_reason",)
AGENT_RUN_STEP_DETAIL_CONTENT = ("reason",)  # escalation steps quote the model
EXECUTION_CONTENT = ("message", "escalation_reason")


def withhold(item: BaseModel, fields: Iterable[str]) -> None:
    for name in fields:
        setattr(item, name, None)


def _step_error_is_content(step: RunStepOut) -> bool:
    """A failed tool call's error is written by the tool (or quotes the arguments
    it rejected). Denials, rejections and model errors are written by the platform
    and stay visible as metadata."""
    return step.step_type == RunStepType.TOOL_CALL.value and step.status == (
        RunStepStatus.FAILED.value
    )


def _is_approval_request(step: WorkflowStepRun) -> bool:
    """Whether a step's output is still the approval request a person decides.

    Approval steps only ever hold their request (and its decision). A tool step
    holds its request while waiting or once rejected; after approval its output
    becomes the tool's result, which is run content.
    """
    if step.step_type == "approval":
        return True
    return step.step_type == "tool" and step.status in (
        WorkflowStepStatus.WAITING.value,
        WorkflowStepStatus.REJECTED.value,
    )


# --------------------------------------------------------------------------- #
# Presenters
# --------------------------------------------------------------------------- #
def present_workflow_run(run: WorkflowRun, viewer: Viewer) -> WorkflowRunOut:
    out = WorkflowRunOut.model_validate(run)
    if not sees_run_content(viewer, run.initiated_by):
        withhold(out, WORKFLOW_RUN_CONTENT)
        out.content_withheld = True
    return out


def present_workflow_run_detail(
    run: WorkflowRun, steps: Sequence[WorkflowStepRun], viewer: Viewer
) -> WorkflowRunDetail:
    detail = WorkflowRunDetail.model_validate(present_workflow_run(run, viewer).model_dump())
    if not detail.content_withheld:
        detail.context = run.context or {}
        detail.steps = [WorkflowStepRunOut.model_validate(s) for s in steps]
        return detail
    approver = sees_approval_requests(viewer)
    detail.context = None
    detail.steps = []
    for step in steps:
        out = WorkflowStepRunOut.model_validate(step)
        if not (approver and _is_approval_request(step)):
            withhold(out, WORKFLOW_STEP_CONTENT)
        detail.steps.append(out)
    return detail


def present_agent_run(run: AgentRun, viewer: Viewer) -> RunOut:
    out = RunOut.model_validate(run)
    if not sees_run_content(viewer, run.initiated_by):
        withhold(out, AGENT_RUN_CONTENT)
        out.content_withheld = True
    return out


def present_agent_run_detail(
    run: AgentRun, steps: Sequence[AgentRunStep], viewer: Viewer
) -> RunDetailOut:
    detail = RunDetailOut.model_validate(present_agent_run(run, viewer).model_dump())
    detail.steps = [RunStepOut.model_validate(s) for s in steps]
    if detail.content_withheld:
        for step in detail.steps:
            if _step_error_is_content(step):
                step.error = None
            if step.detail:
                step.detail = {
                    k: v for k, v in step.detail.items() if k not in AGENT_RUN_STEP_DETAIL_CONTENT
                }
    return detail


def present_execution(
    execution: dict[str, Any], acting_user_id: uuid.UUID | None, viewer: Viewer
) -> dict[str, Any]:
    """An agent execution result shown to someone other than its person (e.g. the
    approver who resumed it) carries its status and ids, not the agent's answer."""
    visible = sees_run_content(viewer, acting_user_id)
    shown = dict(execution)
    if not visible:
        for name in EXECUTION_CONTENT:
            shown[name] = None
    shown["content_withheld"] = not visible
    return shown


def present_escalation_reason(
    reason: str | None, acting_user_id: uuid.UUID | None, viewer: Viewer
) -> str | None:
    return reason if sees_run_content(viewer, acting_user_id) else None
