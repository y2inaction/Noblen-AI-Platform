# Workflow Engine

**Status:** ⬜ Not implemented. Planned for M5.

```
Trigger ─► Condition ─► Agent ─► Tool ─► Verification ─► Approval ─► Action ─► Audit
```

The workflow engine will reuse what exists instead of duplicating it:

- **Agent step:** an agent execution, which is an `AgentRun` with its trace, usage
  and escalation.
- **Tool step:** a registered handler, with the same authorization, validation and
  risk policy.
- **Approval step:** `Approval` + resume.
- **Audit:** `audit_logs` + run traces.

The first iteration will be the smallest production-quality abstraction: versioned
`Workflow`, `WorkflowRun` and `WorkflowStepRun`, with sequential steps, branching,
per-step retries with backoff, failure handling (fail, escalate or continue),
approval steps, manual/event triggers, and scheduled triggers on a worker
(Celery + beat per the existing architecture). A visual builder and complex DAGs
are deferred.
