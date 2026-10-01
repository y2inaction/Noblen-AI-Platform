"""Workflow execution: advance a run step by step, durably (Noblen AI 3.0, M5).

The engine is called by the worker with a claimed (RUNNING) run. It executes
steps until the run finishes or has to wait, committing after every step so a
crash never loses or repeats finished work.

Controls, reused from the agent engine rather than re-invented:

- **Authority:** before every step, the person the run acts for must still be an
  active member holding `workflow:run` (and `agent:run` for agent steps, and the
  tool's `required_permission` for tool steps).
- **Agent steps** are ordinary `AgentRun`s: same trace, tool policy, approvals
  and escalation. If the agent waits for an approval, the workflow waits too.
- **Tool steps** call registered handlers with validated arguments. HIGH-risk
  tools always pause for a human decision first.
- **Retries** with exponential backoff, then `on_failure`: fail, continue or
  escalate (operators are notified).
- **Kill switch:** runs of a paused or archived workflow are cancelled when they
  are next picked up (test runs excepted).
- **Budget:** at most WORKFLOW_MAX_STEPS_PER_RUN executed steps per run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import conversations as conversation_service
from app.agents import provenance
from app.agents.provenance import ProvenanceCollector
from app.agents.publication import is_publication_sink, is_restricted_publication
from app.agents.runtime import AgentRuntime
from app.agents.tools.base import ToolContext
from app.agents.tools.registry import ToolRegistry, tool_registry, validate_arguments
from app.ai.errors import AIError
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.integrations.service import IntegrationGateway
from app.models.enums import (
    MembershipStatus,
    RunStatus,
    ToolRiskLevel,
    WorkflowRunStatus,
    WorkflowStatus,
    WorkflowStepStatus,
)
from app.models.membership import OrganizationMember
from app.models.organization import Organization
from app.models.run import AgentRun
from app.models.workflow import Workflow, WorkflowRun, WorkflowStepRun, WorkflowVersion
from app.rbac.permissions import Permission, role_has_permission
from app.rbac.visibility import publication_attribution
from app.services import work_service
from app.services.audit_service import record_audit
from app.services.work_service import AgentWorkspace
from app.workflows import service
from app.workflows.definition import (
    END,
    AgentStep,
    ApprovalStep,
    ConditionStep,
    ToolStep,
    WorkflowDefinition,
    parse,
)
from app.workflows.templating import TemplateError, references, render

logger = get_logger("workflows.engine")

_AGENT_TERMINAL = {RunStatus.COMPLETED.value, RunStatus.ESCALATED.value, RunStatus.FAILED.value}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Outcome:
    """What a step produced: done (with output and where to go), waiting, or failed."""

    kind: str  # "done" | "wait" | "fail"
    output: dict[str, Any] = field(default_factory=dict)
    goto: str | None = None  # explicit next step for branches; None = the default
    error: str | None = None
    retryable: bool = False


def _templates(step: Any) -> list[Any]:
    """The values a step renders from the run context."""
    if isinstance(step, ConditionStep):
        return [step.left, step.right]
    if isinstance(step, ApprovalStep):
        return [step.title, step.details]
    if isinstance(step, ToolStep):
        return [step.arguments]
    if isinstance(step, AgentStep):
        return [step.input]
    return []


def _merge_into(row: Any, sources: list[Any] | None, truncated: bool) -> None:
    """Union `sources` into a row's provenance. Deterministic (existing order
    first), bounded, and monotone: unknown or truncated input truncates the row,
    and a truncated row stays truncated."""
    collector = ProvenanceCollector()
    collector.inherit(row.sources, row.sources_truncated)
    collector.inherit(sources, truncated)
    row.sources, row.sources_truncated = collector.snapshot()


def _take_agent_provenance(step_run: WorkflowStepRun, agent_run: AgentRun | None) -> None:
    """An agent step's sources are its agent run's sources (M8). An agent run with
    unknown provenance leaves the step unknown too (truncated, fails closed)."""
    if agent_run is None:
        return
    collector = ProvenanceCollector()
    collector.inherit(agent_run.sources, agent_run.sources_truncated)
    step_run.sources, step_run.sources_truncated = collector.snapshot()


def _done(output: dict[str, Any] | None = None, goto: str | None = None) -> _Outcome:
    return _Outcome("done", output or {}, goto)


def _fail(error: str, *, retryable: bool = False) -> _Outcome:
    return _Outcome("fail", error=error[:2000], retryable=retryable)


class WorkflowEngine:
    def __init__(self, runtime: AgentRuntime, tools: ToolRegistry | None = None) -> None:
        self._runtime = runtime
        self._tools = tools or tool_registry

    # ------------------------------------------------------------------ run
    async def advance(self, db: AsyncSession, run: WorkflowRun) -> WorkflowRun:
        workflow = await db.get(Workflow, run.workflow_id)
        version = await db.get(WorkflowVersion, run.version_id)
        assert workflow is not None and version is not None
        definition = parse(version.definition)
        run.status = WorkflowRunStatus.RUNNING.value
        run.started_at = run.started_at or _now()
        run.next_attempt_at = None

        token = service.current_depth.set(run.depth)
        try:
            while run.status == WorkflowRunStatus.RUNNING.value:
                if not run.trigger_detail.get("test") and workflow.status != (
                    WorkflowStatus.ACTIVE.value
                ):
                    await self._finish(
                        db,
                        run,
                        WorkflowRunStatus.CANCELLED,
                        "workflow_inactive",
                        f"The workflow is {workflow.status.lower()}.",
                    )
                    break
                if run.current_step is None:
                    await self._finish(db, run, WorkflowRunStatus.COMPLETED)
                    break
                if run.steps_executed >= settings.WORKFLOW_MAX_STEPS_PER_RUN:
                    await self._finish(
                        db,
                        run,
                        WorkflowRunStatus.FAILED,
                        "step_budget_exceeded",
                        "The run exceeded its step budget (a loop?).",
                    )
                    break
                await self._run_step(db, run, workflow, definition)
                await db.commit()
        finally:
            service.current_depth.reset(token)
        await db.commit()
        return run

    async def _run_step(
        self, db: AsyncSession, run: WorkflowRun, workflow: Workflow, definition: WorkflowDefinition
    ) -> None:
        assert run.current_step is not None
        step = definition.step(run.current_step)
        step_run = await self._resumable(db, run)
        authority = await self._authority(db, run, step)
        if authority is not None:
            # Lost authority always stops the run, whatever the step's failure policy.
            step_run = step_run or self._new_step_run(db, run, step)
            step_run.status = WorkflowStepStatus.FAILED.value
            step_run.error = authority
            step_run.finished_at = _now()
            await self._finish(db, run, WorkflowRunStatus.FAILED, "not_authorized", authority)
            return
        if step_run is None:
            step_run = self._new_step_run(db, run, step)
            run.steps_executed += 1
        try:
            outcome = await self._execute(db, run, workflow, step, step_run)
        except TemplateError as exc:
            outcome = _fail(str(exc))
        await self._propagate(db, run, step, step_run)
        await self._apply(db, run, workflow, definition, step, step_run, outcome)

    # ------------------------------------------------------------- provenance
    async def _propagate(
        self, db: AsyncSession, run: WorkflowRun, step: Any, step_run: WorkflowStepRun
    ) -> None:
        """Transitive provenance (M8.6).

        A step's content derives from what it observed itself (M8.5 capture) and
        from every upstream value its templates consume: `steps.<id>.*` inherits
        that step's latest earlier provenance, and `input.*` is the run's external
        input. An agent step passes this on to its agent run, whose content was
        produced from the rendered input. The workflow run is the union of its
        input and all its steps. Unknown or truncated provenance anywhere upstream
        makes every consumer truncated; nothing is ever reset to empty.
        """
        inherited, inherited_truncated = await self._consumed(db, run, step, step_run)

        if step_run.agent_run_id is not None:
            agent_run = await db.get(AgentRun, step_run.agent_run_id)
            if agent_run is not None:
                _merge_into(agent_run, inherited, inherited_truncated)
        _merge_into(step_run, inherited, inherited_truncated)
        _merge_into(run, step_run.sources, step_run.sources_truncated)

    async def _consumed(
        self, db: AsyncSession, run: WorkflowRun, step: Any, step_run: WorkflowStepRun
    ) -> tuple[list[dict[str, str]], bool]:
        """The provenance of the upstream values this step's templates consume."""
        consumed = ProvenanceCollector()
        for path in references(_templates(step)):
            root, _, rest = path.partition(".")
            if root == "input" and run.input:
                consumed.add(provenance.external_input_ref())
            elif root == "steps":
                upstream = await self._latest_step_run(db, run, rest.split(".", 1)[0], step_run)
                if upstream is not None:
                    consumed.inherit(upstream.sources, upstream.sources_truncated)
        return consumed.snapshot()

    @staticmethod
    async def _latest_step_run(
        db: AsyncSession, run: WorkflowRun, step_id: str, current: WorkflowStepRun
    ) -> WorkflowStepRun | None:
        """The attempt of `step_id` whose output is in the run context: the most
        recent one before the current step."""
        stmt = select(WorkflowStepRun).where(
            WorkflowStepRun.run_id == run.id, WorkflowStepRun.step_id == step_id
        )
        if current.sequence is not None:
            stmt = stmt.where(WorkflowStepRun.sequence < current.sequence)
        return (
            await db.execute(stmt.order_by(WorkflowStepRun.sequence.desc()).limit(1))
        ).scalar_one_or_none()

    def _new_step_run(self, db: AsyncSession, run: WorkflowRun, step: Any) -> WorkflowStepRun:
        step_run = WorkflowStepRun(
            organization_id=run.organization_id,
            run_id=run.id,
            sequence=run.steps_executed + 1,
            step_id=step.id,
            step_type=step.type,
            attempt=run.current_attempt,
            status=WorkflowStepStatus.RUNNING.value,
            started_at=_now(),
            # Provenance (M8): filled by the step's capture points below.
            sources=[],
            sources_truncated=False,
        )
        db.add(step_run)
        return step_run

    async def _resumable(self, db: AsyncSession, run: WorkflowRun) -> WorkflowStepRun | None:
        """A WAITING attempt of the current step (resuming after a decision/agent)."""
        return (
            await db.execute(
                select(WorkflowStepRun)
                .where(
                    WorkflowStepRun.run_id == run.id,
                    WorkflowStepRun.step_id == run.current_step,
                    WorkflowStepRun.attempt == run.current_attempt,
                    WorkflowStepRun.status == WorkflowStepStatus.WAITING.value,
                )
                .order_by(WorkflowStepRun.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _authority(self, db: AsyncSession, run: WorkflowRun, step: Any) -> str | None:
        """None if the run's person may still do this step, else why not."""
        if run.initiated_by is None:
            return "The workflow has no person to act for."
        role = (
            await db.execute(
                select(OrganizationMember.role_name).where(
                    OrganizationMember.organization_id == run.organization_id,
                    OrganizationMember.user_id == run.initiated_by,
                    OrganizationMember.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if role is None:
            return "The person this workflow acts for is no longer an active member."
        needed = [Permission.WORKFLOW_RUN]
        if isinstance(step, AgentStep):
            needed.append(Permission.AGENT_RUN)
        if isinstance(step, ToolStep):
            handler = self._tools.get_by_name(step.tool)
            if handler is not None and handler.required_permission:
                needed.append(handler.required_permission)
        for permission in needed:
            if not role_has_permission(role, permission):
                return f"The person this workflow acts for lacks '{permission}'."
        return None

    # ------------------------------------------------------------- steps
    def _context(self, run: WorkflowRun) -> dict[str, Any]:
        return {
            "input": run.input or {},
            "trigger": {"type": run.trigger_type.lower(), **(run.trigger_detail or {})},
            "steps": (run.context or {}).get("steps", {}),
            "run": {"id": str(run.id), "workflow_id": str(run.workflow_id)},
        }

    async def _execute(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        step: Any,
        step_run: WorkflowStepRun,
    ) -> _Outcome:
        if isinstance(step, ConditionStep):
            return self._condition(run, step)
        if isinstance(step, ApprovalStep):
            return await self._approval(db, run, workflow, step, step_run)
        if isinstance(step, ToolStep):
            return await self._tool(db, run, workflow, step, step_run)
        if isinstance(step, AgentStep):
            return await self._agent(db, run, workflow, step, step_run)
        raise AssertionError(step)  # pragma: no cover

    def _condition(self, run: WorkflowRun, step: ConditionStep) -> _Outcome:
        context = self._context(run)
        try:
            left = render(step.left, context)
        except TemplateError:
            if step.op not in ("exists", "empty"):
                raise
            left = None
        right = render(step.right, context)
        result = _compare(step.op, left, right)
        return _done({"result": result}, goto=step.then if result else step.else_)

    async def _approval(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        step: ApprovalStep,
        step_run: WorkflowStepRun,
    ) -> _Outcome:
        if step_run.status == WorkflowStepStatus.WAITING.value:
            return self._decided(step_run, reject_to=step.on_reject)
        context = self._context(run)
        title = str(render(step.title, context))
        details = render(step.details, context) if step.details else None
        return await self._ask(db, run, workflow, step_run, title, details)

    async def _ask(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        step_run: WorkflowStepRun,
        title: str,
        details: Any,
    ) -> _Outcome:
        step_run.output = {"title": title, "details": details}
        await work_service.notify_permission_holders(
            db,
            run.organization_id,
            Permission.AGENT_APPROVE_ACTIONS,
            kind="workflow_approval_requested",
            title=f"{workflow.name}: {title}"[:255],
            body=details if isinstance(details, str) else None,
            link={"type": "workflow_run", "id": str(run.id)},
        )
        return _Outcome("wait")

    def _decided(self, step_run: WorkflowStepRun, reject_to: str | None) -> _Outcome:
        if step_run.decision is None:
            return _Outcome("wait")
        output = {**(step_run.output or {}), "decision": step_run.decision}
        if step_run.decision == "approved":
            return _done(output)
        step_run.status = WorkflowStepStatus.REJECTED.value
        return _Outcome("rejected", output, goto=reject_to)

    async def _tool(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        step: ToolStep,
        step_run: WorkflowStepRun,
    ) -> _Outcome:
        handler = self._tools.get_by_name(step.tool)
        if handler is None or not handler.available_in_workflows:
            return _fail(f"Tool '{step.tool}' is not available to workflows.")
        arguments = render(step.arguments, self._context(run))
        if not isinstance(arguments, dict):
            return _fail("Tool arguments must be an object.")
        if error := validate_arguments(handler.input_schema, arguments):
            return _fail(f"Invalid arguments for '{step.tool}': {error}")

        if step_run.status != WorkflowStepStatus.WAITING.value and is_publication_sink(handler):
            # Restricted publication (M9, ADR-0038): decided before the tool runs,
            # on what the step consumes. A waiting step keeps its marker, so a
            # decision is always honoured.
            step_run.restricted_publication = await is_restricted_publication(
                db, run.organization_id, handler, *await self._consumed(db, run, step, step_run)
            )
        needs_approval = (
            step.require_approval
            or handler.risk_level == ToolRiskLevel.HIGH.value
            or step_run.restricted_publication
        )
        if needs_approval:
            if step_run.status != WorkflowStepStatus.WAITING.value:
                return await self._ask(
                    db, run, workflow, step_run, f"Run '{step.tool}'?", {"arguments": arguments}
                )
            decided = self._decided(step_run, reject_to=None)
            if decided.kind != "done":
                return decided

        collector = ProvenanceCollector()
        context = ToolContext(
            organization_id=run.organization_id,
            user_id=run.initiated_by,
            agent_id=None,
            conversation_id=None,
            org_settings=await self._org_settings(db, run.organization_id),
            workspace=AgentWorkspace(
                db=db,
                organization_id=run.organization_id,
                agent_id=None,
                run_id=None,
                user_id=run.initiated_by,
            ),
            integrations=IntegrationGateway(
                db=db,
                organization_id=run.organization_id,
                user_id=run.initiated_by,
                provenance=collector,
            ),
            provenance=collector,
        )
        try:
            result = await handler.execute(context, arguments)
        except Exception as exc:  # noqa: BLE001 - never leak internals
            return _fail(f"Tool '{step.tool}' failed ({type(exc).__name__}).", retryable=True)
        finally:
            # Provenance (M8): what this tool step itself observed (connections).
            step_run.sources, step_run.sources_truncated = collector.snapshot()
        if not result.ok:
            return _fail(result.error or f"Tool '{step.tool}' failed.")
        if handler.risk_level != ToolRiskLevel.LOW.value:
            # Publication attribution (M8.8): the tool published content derived
            # from what this step consumed upstream and what it observed itself.
            published = ProvenanceCollector()
            published.inherit(*await self._consumed(db, run, step, step_run))
            published.inherit(step_run.sources, step_run.sources_truncated)
            await record_audit(
                db,
                action="workflow.tool_executed",
                user_id=run.initiated_by,
                organization_id=run.organization_id,
                target_type="workflow_run",
                target_id=str(run.id),
                metadata={
                    "step": step.id,
                    "tool": step.tool,
                    "acting_role": run.acting_role,
                    **await publication_attribution(db, run.organization_id, *published.snapshot()),
                },
            )
        return _done(result.output)

    async def _agent(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        step: AgentStep,
        step_run: WorkflowStepRun,
    ) -> _Outcome:
        if step_run.agent_run_id is not None:
            return await self._agent_result(db, step_run.agent_run_id, step_run)
        assert run.initiated_by is not None
        message = str(render(step.input, self._context(run)))
        conversation = await conversation_service.create_conversation(
            db,
            run.organization_id,
            run.initiated_by,
            agent_id=step.agent_id,
            title=f"Workflow: {workflow.name}"[:255],
        )
        try:
            result = await self._runtime.execute(
                db,
                organization_id=run.organization_id,
                user_id=run.initiated_by,
                agent_id=step.agent_id,
                conversation_id=conversation.id,
                input_message=message,
                # A fresh conversation holding only the rendered step input: its
                # provenance is what the step consumed, which the run starts with.
                conversation_is_source=False,
                inherited=await self._consumed(db, run, step, step_run),
            )
        except AIError as exc:
            return _fail(
                f"The agent's model call failed ({exc.error_code}).", retryable=exc.retryable
            )
        except AppError as exc:
            return _fail(exc.message)
        step_run.agent_run_id = result.run_id
        _take_agent_provenance(step_run, await db.get(AgentRun, result.run_id))
        if result.status == "completed":
            return _done(
                {
                    "text": (result.message or {}).get("content", ""),
                    "agent_run_id": str(result.run_id),
                }
            )
        if result.status == "awaiting_approval":
            return _Outcome("wait")
        return _fail(f"The agent escalated: {result.escalation_reason or 'no reason given'}")

    async def _agent_result(
        self, db: AsyncSession, agent_run_id: uuid.UUID, step_run: WorkflowStepRun
    ) -> _Outcome:
        agent_run = await db.get(AgentRun, agent_run_id)
        if agent_run is None:
            return _fail("The agent run no longer exists.")
        _take_agent_provenance(step_run, agent_run)
        if agent_run.status not in _AGENT_TERMINAL:
            return _Outcome("wait")
        if agent_run.status == RunStatus.COMPLETED.value and agent_run.conversation_id:
            messages = await conversation_service.get_messages(
                db, agent_run.organization_id, agent_run.conversation_id
            )
            answer = next(
                (
                    m.content
                    for m in reversed(messages)
                    if m.role == "assistant"
                    and m.content
                    and m.sequence >= agent_run.context_start_sequence
                ),
                "",
            )
            return _done({"text": answer, "agent_run_id": str(agent_run.id)})
        if agent_run.status == RunStatus.ESCALATED.value:
            return _fail("The agent escalated to a human.")
        return _fail("The agent run failed.")

    async def _org_settings(self, db: AsyncSession, organization_id: uuid.UUID) -> dict[str, Any]:
        org = await db.get(Organization, organization_id)
        if org is None:  # pragma: no cover
            return {}
        return {
            "name": org.name,
            "currency": org.currency,
            "timezone": org.timezone,
            "locale": org.locale,
        }

    # ------------------------------------------------------------ outcomes
    async def _apply(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        workflow: Workflow,
        definition: WorkflowDefinition,
        step: Any,
        step_run: WorkflowStepRun,
        outcome: _Outcome,
    ) -> None:
        if outcome.kind == "wait":
            step_run.status = WorkflowStepStatus.WAITING.value
            run.status = WorkflowRunStatus.WAITING.value
            return

        step_run.finished_at = _now()
        if outcome.kind == "rejected":
            self._record(run, step, "rejected", outcome.output)
            step_run.output = outcome.output
            if outcome.goto is None:
                await self._finish(
                    db,
                    run,
                    WorkflowRunStatus.CANCELLED,
                    "approval_rejected",
                    f"Step '{step.id}' was rejected by a reviewer.",
                )
            else:
                self._move(run, outcome.goto)
            return

        if outcome.kind == "done":
            step_run.status = WorkflowStepStatus.SUCCEEDED.value
            step_run.output = outcome.output
            self._record(run, step, "succeeded", outcome.output)
            self._move(run, outcome.goto or definition.next_of(step) or END)
            return

        # Failure: retry with backoff, then apply the step's failure policy.
        step_run.status = WorkflowStepStatus.FAILED.value
        step_run.error = outcome.error
        if outcome.retryable and run.current_attempt < step.retry.max_attempts:
            delay = step.retry.backoff_seconds * (2 ** (run.current_attempt - 1))
            run.current_attempt += 1
            run.status = WorkflowRunStatus.QUEUED.value
            run.next_attempt_at = _now() + timedelta(seconds=delay)
            return
        self._record(run, step, "failed", {"error": outcome.error})
        if step.on_failure == "continue":
            self._move(run, definition.next_of(step) or END)
        elif step.on_failure == "escalate":
            await self._finish(db, run, WorkflowRunStatus.ESCALATED, "step_failed", outcome.error)
            await work_service.notify_permission_holders(
                db,
                run.organization_id,
                Permission.AGENT_OPERATE,
                kind="workflow_escalated",
                title=f"Workflow needs attention: {workflow.name}"[:255],
                body=f"Step '{step.id}' failed.",
                link={"type": "workflow_run", "id": str(run.id)},
                also=run.initiated_by,
                participant_body=f"Step '{step.id}' failed: {outcome.error}",
            )
        else:
            await self._finish(db, run, WorkflowRunStatus.FAILED, "step_failed", outcome.error)

    @staticmethod
    def _record(run: WorkflowRun, step: Any, status: str, output: dict[str, Any]) -> None:
        context = dict(run.context or {})
        steps = dict(context.get("steps", {}))
        steps[step.id] = {"status": status, "output": output}
        context["steps"] = steps
        run.context = context  # reassign so the JSON column is marked dirty

    @staticmethod
    def _move(run: WorkflowRun, target: str) -> None:
        run.current_step = None if target == END else target
        run.current_attempt = 1

    async def _finish(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        status: WorkflowRunStatus,
        code: str | None = None,
        error: str | None = None,
    ) -> None:
        run.status = status.value
        run.error_code = code
        run.error = error
        run.finished_at = _now()
        await record_audit(
            db,
            action="workflow.run_finished",
            user_id=run.initiated_by,
            organization_id=run.organization_id,
            target_type="workflow_run",
            target_id=str(run.id),
            metadata={"status": status.value, "error_code": code},
        )
        logger.info("workflow_run_finished", run_id=str(run.id), status=status.value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "exists":
        return left is not None
    if op == "empty":
        return left in (None, "", [], {}, 0)
    if op == "contains":
        if isinstance(left, list | dict):
            return right in left
        return str(right).lower() in str(left).lower()
    if op in ("eq", "ne"):
        ln, rn = _number(left), _number(right)
        equal = ln == rn if ln is not None and rn is not None else str(left) == str(right)
        return equal if op == "eq" else not equal
    ln, rn = _number(left), _number(right)
    if ln is None or rn is None:
        raise TemplateError(f"'{op}' compares numbers; got {left!r} and {right!r}.")
    return {"gt": ln > rn, "gte": ln >= rn, "lt": ln < rn, "lte": ln <= rn}[op]
