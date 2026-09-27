# Workflows (original design)

> **Implemented in Noblen AI 3.0 M5** as a smaller, production-quality engine: see
> [`architecture/workflows.md`](architecture/workflows.md) for what exists. Webhook,
> form, payment and lead triggers and HTTP/email/database nodes are not implemented
> yet. They need integrations (M6).

A visual workflow engine: `Trigger → Node → Node → Condition → Action`, with every
execution logged.

## Triggers
webhook · form submission · schedule · new lead · new message · payment ·
manual execution.

## Nodes
AI generate · AI classify · AI extract · AI summarize · condition · HTTP request ·
database action · email · notification · task creation.

## Status
`DRAFT → ACTIVE → PAUSED → ARCHIVED`.

## Execution
- Each run creates a `workflow_run` with per-step `workflow_run_logs`.
- Long-running/scheduled runs execute on Celery workers + beat.
- Node execution is idempotent where possible; failures are captured with
  structured errors and surfaced to operators (and to the Operations agent).

## Models (planned)
`workflows`, `workflow_versions`, `workflow_nodes`, `workflow_runs`,
`workflow_run_logs`.
