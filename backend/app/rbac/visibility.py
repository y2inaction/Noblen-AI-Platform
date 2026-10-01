"""Who may see the *content* of run-like resources (ADR-0035, ADR-0036, ADR-0037).

A run acts for exactly one person (the starter of a manual run, the activator of a
triggered workflow) and reads with that person's authority: their private memories,
knowledge restricted to them, their integrations. What a run was given, retrieved
and produced is run content. Its person always sees it.

Since Milestone 8 a run records references to the sources its content derives from
(`sources`, `app.agents.provenance`). Anyone else sees the content only when they
can read **every** recorded source *now* (`can_read_sources`), decided against their
current permissions at read time. One unreadable source withholds it
(`restricted_sources`). Unknown provenance withholds it too (`unknown_provenance`):
a run from before M8 (`sources IS NULL`), truncated provenance, or a reference that
is malformed, deleted, cyclic or in another organization. An empty source list
proves nothing either and is unknown too. Roles never bypass this;
`knowledge:read_all` is the one exception, and only for knowledge sources.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import conversations, provenance
from app.knowledge.access import Principal, readable_documents_query, resolve_principal
from app.models.conversation import Conversation
from app.models.enums import RunStepStatus, RunStepType, WorkflowStepStatus
from app.models.integration import IntegrationConnection
from app.models.knowledge import KnowledgeDocument, KnowledgeTable
from app.models.memory import Memory
from app.models.run import AgentRun, AgentRunStep
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.rbac.permissions import Permission, role_has_permission
from app.schemas.run import RunDetailOut, RunOut, RunStepOut
from app.schemas.workflow import WorkflowRunDetail, WorkflowRunOut, WorkflowStepRunOut
from app.services.memory_service import _fresh, _visible_to_member, retention_days


@dataclass(frozen=True)
class Viewer:
    """The person a response is for, in the active organization."""

    user_id: uuid.UUID
    role_name: str
    is_superuser: bool = False

    def may(self, permission: str) -> bool:
        return self.is_superuser or role_has_permission(self.role_name, permission)


# --------------------------------------------------------------------------- #
# Provenance: current-time source authorization (M8, ADR-0037)
# --------------------------------------------------------------------------- #
RESTRICTED_SOURCES = "restricted_sources"
UNKNOWN_PROVENANCE = "unknown_provenance"

# Per-reference outcomes, ordered so that the worst one wins.
_READABLE, _RESTRICTED, _UNKNOWN = 0, 1, 2
_REASONS = {_RESTRICTED: RESTRICTED_SOURCES, _UNKNOWN: UNKNOWN_PROVENANCE}

# References to other runs are followed at most this many levels deep; deeper
# provenance is unknown. Each level costs one query per run type, never per reference.
MAX_PROVENANCE_DEPTH = 4

_RUN_REFERENCES: dict[str, Any] = {
    provenance.AGENT_RUN: AgentRun,
    provenance.WORKFLOW_STEP: WorkflowStepRun,
}

_Key = tuple[str, str]
_Provenance = tuple[Sequence[Mapping[str, Any]] | None, bool]


def _ref(raw: Any) -> _Key | None:
    """A stored reference as (type, id), or None when it no longer validates."""
    try:
        ref = provenance.normalize(raw)
    except ValueError:
        return None
    return ref["type"], ref.get("id", "")


@dataclass
class SourceAccess:
    """What one viewer can read now among a set of provenance references.

    Built by `resolve_source_access`, which does all the database work up front.
    Evaluating a run's provenance against it is pure. A reference that was not
    resolved (deleted, in another organization, malformed, too deep) is unknown.
    """

    leaves: dict[_Key, int] = field(default_factory=dict)
    runs: dict[_Key, _Provenance] = field(default_factory=dict)
    _memo: dict[_Key, int] = field(default_factory=dict)

    def _run_status(self, key: _Key, stack: frozenset[_Key]) -> int:
        if key in stack:
            return _UNKNOWN  # a cycle: this run's content derives from itself
        if key not in self.runs:
            # Not resolved: missing or deleted, in another organization, or
            # deeper than MAX_PROVENANCE_DEPTH (never loaded).
            return _UNKNOWN
        if key not in self._memo:
            sources, truncated = self.runs[key]
            self._memo[key] = self.status(sources, truncated, stack | {key})
        return self._memo[key]

    def status(
        self,
        sources: Sequence[Mapping[str, Any]] | None,
        truncated: bool,
        stack: frozenset[_Key] = frozenset(),
    ) -> int:
        # Nothing recorded proves nothing: NULL (before M8), [] and truncated
        # provenance are all unknown.
        if not sources or truncated:
            return _UNKNOWN
        worst = _READABLE
        for raw in sources:
            key = _ref(raw)
            if key is None:
                return _UNKNOWN
            if key[0] in _RUN_REFERENCES:
                outcome = self._run_status(key, stack)
            else:
                outcome = self.leaves.get(key, _UNKNOWN)
            worst = max(worst, outcome)
            if worst == _UNKNOWN:
                break
        return worst

    def reason(self, sources: Sequence[Mapping[str, Any]] | None, truncated: bool) -> str | None:
        """None when every source is readable, else why the content is withheld."""
        return _REASONS.get(self.status(sources, truncated))


async def resolve_source_access(
    db: AsyncSession,
    viewer: Viewer,
    organization_id: uuid.UUID,
    provenances: Iterable[_Provenance],
    *,
    principal: Principal | None = None,
) -> SourceAccess:
    """Resolve, for `viewer`, every reference in `provenances` in bulk.

    `principal` is the viewer's knowledge principal. It is looked up from the
    viewer's current membership unless given (publication attribution passes a
    baseline member that holds nothing).

    The checks are the platform's existing ones, evaluated with the viewer's
    permissions at this moment:

    - knowledge documents: `readable_documents_query` (knowledge-base and document
      ACLs; `knowledge:read_all` is the only bypass). A table follows its document.
    - memories: the member visibility rule of the memory API, on the stored row
      (never the reference's scope label), with retention applied.
    - integrations: `integration:use`.
    - external input: any member of the organization.
    - conversations: the conversation-participant rule of the conversation API.
    - agent runs and workflow steps: their own recorded provenance, followed at
      most `MAX_PROVENANCE_DEPTH` levels.

    Everything is looked up inside `organization_id`. Whatever cannot be resolved
    is unknown and fails closed: a missing or deleted row, a row of another tenant,
    a malformed reference, a run reference that forms a cycle or lies deeper than
    `MAX_PROVENANCE_DEPTH`, and empty, NULL or truncated provenance.

    Cost: one query per reference type present, plus one per run type for each
    level of run nesting (at most `MAX_PROVENANCE_DEPTH`). It never depends on the
    number of references.
    """
    access = SourceAccess()
    wanted: dict[str, set[str]] = {}
    frontier: list[Sequence[Mapping[str, Any]] | None] = [s for s, _ in provenances]
    for depth in range(MAX_PROVENANCE_DEPTH + 1):
        runs: dict[str, set[str]] = {}
        for sources in frontier:
            for raw in sources or ():
                key = _ref(raw)
                if key is None:
                    continue
                if key[0] in _RUN_REFERENCES:
                    if key not in access.runs:
                        runs.setdefault(key[0], set()).add(key[1])
                else:
                    wanted.setdefault(key[0], set()).add(key[1])
        if not runs or depth == MAX_PROVENANCE_DEPTH:
            break  # run references still pending past the limit stay unresolved
        frontier = []
        for kind, ids in runs.items():
            model = _RUN_REFERENCES[kind]
            rows = await db.execute(
                select(model.id, model.sources, model.sources_truncated).where(
                    model.organization_id == organization_id,
                    model.id.in_([uuid.UUID(i) for i in ids]),
                )
            )
            for row_id, sources, truncated in rows.tuples():
                access.runs[(kind, str(row_id))] = (sources, bool(truncated))
                frontier.append(sources)

    def uuids(kind: str) -> list[uuid.UUID]:
        return [uuid.UUID(i) for i in wanted.get(kind, ())]

    def mark(kind: str, ids: Iterable[Any], outcome: int) -> None:
        for i in ids:
            access.leaves[(kind, str(i))] = outcome

    if provenance.EXTERNAL_INPUT in wanted:
        # The viewer is an active member of the organization (TenantContext).
        mark(provenance.EXTERNAL_INPUT, [""], _READABLE)

    documents = set(uuids(provenance.KNOWLEDGE_DOCUMENT))
    tables: dict[uuid.UUID, uuid.UUID] = {}
    if provenance.KNOWLEDGE_TABLE in wanted:
        rows = await db.execute(
            select(KnowledgeTable.id, KnowledgeTable.document_id).where(
                KnowledgeTable.organization_id == organization_id,
                KnowledgeTable.id.in_(uuids(provenance.KNOWLEDGE_TABLE)),
            )
        )
        tables = dict(rows.tuples().all())
        documents |= set(tables.values())
    if documents:
        if principal is None:
            principal = await resolve_principal(db, organization_id, viewer.user_id)
        existing = set(
            (
                await db.execute(
                    select(KnowledgeDocument.id).where(
                        KnowledgeDocument.organization_id == organization_id,
                        KnowledgeDocument.id.in_(documents),
                    )
                )
            ).scalars()
        )
        readable: set[uuid.UUID] = set(
            (
                await db.execute(
                    readable_documents_query(principal).where(KnowledgeDocument.id.in_(existing))
                )
            ).scalars()
        )
        by_document = {d: (_READABLE if d in readable else _RESTRICTED) for d in existing}
        for d, outcome in by_document.items():
            mark(provenance.KNOWLEDGE_DOCUMENT, [d], outcome)
        for t, d in tables.items():
            if d in by_document:
                mark(provenance.KNOWLEDGE_TABLE, [t], by_document[d])

    if provenance.MEMORY in wanted:
        where: list[Any] = [
            Memory.organization_id == organization_id,
            Memory.id.in_(uuids(provenance.MEMORY)),
        ]
        fresh = _fresh(await retention_days(db, organization_id))
        if fresh is not None:
            where.append(fresh)
        rows = await db.execute(select(Memory.id, _visible_to_member(viewer.user_id)).where(*where))
        for memory_id, visible in rows.tuples():
            mark(provenance.MEMORY, [memory_id], _READABLE if visible else _RESTRICTED)

    if provenance.CONVERSATION in wanted:
        rows = await db.execute(
            select(Conversation.id, conversations.readable_by(viewer.user_id)).where(
                Conversation.organization_id == organization_id,
                Conversation.id.in_(uuids(provenance.CONVERSATION)),
            )
        )
        for conversation_id, visible in rows.tuples():
            mark(provenance.CONVERSATION, [conversation_id], _READABLE if visible else _RESTRICTED)

    if provenance.INTEGRATION in wanted:
        rows = await db.execute(
            select(IntegrationConnection.id).where(
                IntegrationConnection.organization_id == organization_id,
                IntegrationConnection.id.in_(uuids(provenance.INTEGRATION)),
            )
        )
        outcome = _READABLE if viewer.may(Permission.INTEGRATION_USE) else _RESTRICTED
        mark(provenance.INTEGRATION, rows.scalars(), outcome)
    return access


async def can_read_sources(
    db: AsyncSession,
    viewer: Viewer,
    organization_id: uuid.UUID,
    sources: Sequence[Mapping[str, Any]] | None,
    truncated: bool = False,
) -> bool:
    """Whether `viewer` can read every source in `sources` right now."""
    access = await resolve_source_access(db, viewer, organization_id, [(sources, truncated)])
    return access.status(sources, truncated) == _READABLE


# --------------------------------------------------------------------------- #
# Publication attribution (M8.8)
# --------------------------------------------------------------------------- #
async def publication_attribution(
    db: AsyncSession,
    organization_id: uuid.UUID,
    sources: Sequence[Mapping[str, Any]] | None,
    truncated: bool,
) -> dict[str, Any]:
    """Audit metadata for output a run publishes (a task, a memory, a message, an
    external call): how many sources of each type it derives from, and whether it
    is `restricted`.

    `restricted` is false only when a baseline member of the organization (no
    role permissions, no grants, no private data, in no conversation) could read
    every source now. Anything else, including unknown, empty or truncated
    provenance, is restricted. References and counts only, never content.
    """
    counts: dict[str, int] = {}
    for raw in sources or ():
        key = _ref(raw)
        kind = key[0] if key is not None else "invalid"
        counts[kind] = counts.get(kind, 0) + 1
    baseline = Viewer(user_id=uuid.uuid4(), role_name="")
    access = await resolve_source_access(
        db,
        baseline,
        organization_id,
        [(sources, truncated)],
        principal=Principal(organization_id, baseline.user_id, baseline.role_name),
    )
    return {
        "source_counts": counts,
        "sources_truncated": bool(truncated),
        "restricted": access.status(sources, truncated) != _READABLE,
    }


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def sees_run_content(viewer: Viewer, acting_user_id: uuid.UUID | None) -> bool:
    """Participant rule: the person a run acts for always sees its content."""
    return acting_user_id is not None and acting_user_id == viewer.user_id


def withheld_reason(
    viewer: Viewer,
    acting_user_id: uuid.UUID | None,
    sources: Sequence[Mapping[str, Any]] | None,
    truncated: bool,
    access: SourceAccess,
) -> str | None:
    """The M8 rule: participant OR can_read_sources. None means visible."""
    if sees_run_content(viewer, acting_user_id):
        return None
    return access.reason(sources, truncated)


async def _access_for(
    db: AsyncSession, viewer: Viewer, runs: Sequence[AgentRun | WorkflowRun]
) -> SourceAccess:
    """Resolve the provenance of the runs whose content the viewer does not own."""
    others = [r for r in runs if not sees_run_content(viewer, r.initiated_by)]
    if not others:
        return SourceAccess()
    return await resolve_source_access(
        db,
        viewer,
        others[0].organization_id,
        [(r.sources, r.sources_truncated) for r in others],
    )


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
def _workflow_run_out(run: WorkflowRun, viewer: Viewer, access: SourceAccess) -> WorkflowRunOut:
    out = WorkflowRunOut.model_validate(run)
    reason = withheld_reason(viewer, run.initiated_by, run.sources, run.sources_truncated, access)
    if reason is not None:
        withhold(out, WORKFLOW_RUN_CONTENT)
        out.content_withheld = True
        out.content_withheld_reason = reason
    return out


async def present_workflow_runs(
    db: AsyncSession, runs: Sequence[WorkflowRun], viewer: Viewer
) -> list[WorkflowRunOut]:
    access = await _access_for(db, viewer, runs)
    return [_workflow_run_out(r, viewer, access) for r in runs]


async def present_workflow_run(
    db: AsyncSession, run: WorkflowRun, viewer: Viewer
) -> WorkflowRunOut:
    [out] = await present_workflow_runs(db, [run], viewer)
    return out


async def present_workflow_run_detail(
    db: AsyncSession, run: WorkflowRun, steps: Sequence[WorkflowStepRun], viewer: Viewer
) -> WorkflowRunDetail:
    """A workflow run's sources are the union of its steps' (M8.6), so the run's
    decision covers its context and every step output."""
    summary = await present_workflow_run(db, run, viewer)
    detail = WorkflowRunDetail.model_validate(summary.model_dump())
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


def _agent_run_out(run: AgentRun, viewer: Viewer, access: SourceAccess) -> RunOut:
    out = RunOut.model_validate(run)
    reason = withheld_reason(viewer, run.initiated_by, run.sources, run.sources_truncated, access)
    if reason is not None:
        withhold(out, AGENT_RUN_CONTENT)
        out.content_withheld = True
        out.content_withheld_reason = reason
    return out


async def present_agent_runs(
    db: AsyncSession, runs: Sequence[AgentRun], viewer: Viewer
) -> list[RunOut]:
    access = await _access_for(db, viewer, runs)
    return [_agent_run_out(r, viewer, access) for r in runs]


async def present_agent_run(db: AsyncSession, run: AgentRun, viewer: Viewer) -> RunOut:
    [out] = await present_agent_runs(db, [run], viewer)
    return out


async def present_agent_run_detail(
    db: AsyncSession, run: AgentRun, steps: Sequence[AgentRunStep], viewer: Viewer
) -> RunDetailOut:
    summary = await present_agent_run(db, run, viewer)
    detail = RunDetailOut.model_validate(summary.model_dump())
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


async def present_execution(
    db: AsyncSession,
    execution: dict[str, Any],
    organization_id: uuid.UUID,
    acting_user_id: uuid.UUID | None,
    viewer: Viewer,
) -> dict[str, Any]:
    """An agent execution result shown to someone other than its person (e.g. the
    approver who resumed it) carries its status and ids. The agent's answer is
    shown only under the run rule: participant, or every source readable now."""
    reason: str | None = None
    if not sees_run_content(viewer, acting_user_id):
        run_id = _ref({"type": provenance.AGENT_RUN, "id": execution.get("run_id")})
        run = await db.get(AgentRun, uuid.UUID(run_id[1])) if run_id else None
        if run is None or run.organization_id != organization_id:
            reason = UNKNOWN_PROVENANCE
        else:
            access = await resolve_source_access(
                db, viewer, organization_id, [(run.sources, run.sources_truncated)]
            )
            reason = access.reason(run.sources, run.sources_truncated)
    shown = dict(execution)
    if reason is not None:
        for name in EXECUTION_CONTENT:
            shown[name] = None
    shown["content_withheld"] = reason is not None
    shown["content_withheld_reason"] = reason
    return shown


def present_escalation_reason(
    reason: str | None,
    acting_user_id: uuid.UUID | None,
    sources: Sequence[Mapping[str, Any]] | None,
    truncated: bool,
    viewer: Viewer,
    access: SourceAccess,
) -> str | None:
    """An escalation reason is the model's words: run content."""
    if withheld_reason(viewer, acting_user_id, sources, truncated, access) is None:
        return reason
    return None
