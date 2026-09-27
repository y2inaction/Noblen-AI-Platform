# Observability and AI Operations

**Status:** 🟡 Backend foundations implemented (M1). There are no dashboards or
metrics exporter yet.

The goal is to answer: *"How well is this AI workforce actually performing?"*

## Signals

| Signal | Where |
|---|---|
| Request latency, request id | structured logs (middleware) |
| Model calls: provider, model, tokens, cost, latency, fallback | `ai_usage_records` (Phase 2) + `MODEL_CALL` run steps (M1) |
| Tool calls: status (`SUCCEEDED`/`FAILED`/`DENIED`), risk, latency | `TOOL_CALL` run steps |
| Approvals, escalations | `approvals`, `APPROVAL` / `ESCALATION` run steps |
| Run outcome, duration, totals | `agent_runs` |
| Security-relevant actions | `audit_logs` |

Log lines carry `request_id`, `organization_id`, `user_id`, `agent_id`,
`agent_version_id` and `run_id`.

## API (M1)

| Endpoint | Permission | Returns |
|---|---|---|
| `GET /api/v1/runs?agent_id&status` | `run:view` | paginated runs |
| `GET /api/v1/runs/{id}` | `run:view` | run + step trace |
| `GET /api/v1/operations/overview?window_days=30` | `operations:view` | see below |

The overview returns:

- agents by status; runs by status and total;
- **success, escalation and failure rates** (over concluded runs), and average run duration;
- pending approvals, and approvals by status;
- tool calls, tool failures and tool denials;
- tokens, estimated cost, and usage by provider and model;
- the 10 most recent escalations with their reasons.

## Planned

OpenTelemetry/Prometheus export, per-agent trends, anomaly flags (spend or denial
spikes), and the operator dashboard in the frontend.
