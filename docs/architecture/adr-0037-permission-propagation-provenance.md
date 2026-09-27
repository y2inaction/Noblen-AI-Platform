# ADR-0037 — Permission propagation through provenance references

**Status:** Proposed (security review, 2026-09). Not implemented. Follows ADR-0035 and ADR-0036.

## Context

The chain of custody for any agent result is:

```
Human ─▶ Agent run / Workflow run ─▶ Tool ─▶ Knowledge │ Memory │ Integration │ DB
   ─▶ Result ─▶ Persistence (message, step output, task, memory, email) ─▶ Viewer
```

Authority propagates correctly *down* the chain today. Each read is checked as the
acting person at execution time: M3 ACLs, memory scopes, the tool permission ceiling,
`_authority` before each workflow step, and the integration gateway.

Nothing propagates back *up* with the result. Once text is persisted, it no longer
knows what it was derived from, so the read side (ADR-0035) can only choose between
"everyone" (today's leak) and "only the participant" (the ADR-0035 fix). What is
missing is a record of which protected things a piece of content came from.

## Decision (proposed)

### 1. Record references, never copies

Every run records a bounded list of **source references**: the protected inputs its
content may derive from. Content is never duplicated into provenance. A reference
names a row the platform already authorizes.

```json
{"type": "knowledge_document", "id": "…"}
{"type": "knowledge_table",    "id": "…"}
{"type": "memory",             "id": "…", "scope": "USER"}
{"type": "integration",        "id": "…"}          // connection used by a tool
{"type": "workflow_step",      "id": "…"}          // an upstream WorkflowStepRun
{"type": "agent_run",          "id": "…"}
{"type": "external_input"}                          // webhook/event/manual payload
```

The rest of the provenance envelope already exists on the rows. It is **not
duplicated**; it is referenced:

| Provenance field | Where it already lives |
|---|---|
| organization | `organization_id` on every row |
| user / initiator | `agent_runs.initiated_by`, `workflow_runs.initiated_by`, `workflows.run_as_user_id` |
| agent | `agent_runs.agent_id`, `agent_version_id` |
| workflow | `workflow_runs.workflow_id`, `version_id`, `workflow_step_runs.step_id` |
| tool | `agent_run_steps.name` (tool calls), approvals `tool_name` |
| timestamp | `created_at` / `started_at` / `finished_at` |
| **authorization context** | **new:** `acting_role` (the acting person's role when the run started) |
| **sources** | **new:** `sources` (list above) |

New columns: `agent_runs.sources JSON`, `agent_runs.acting_role`,
`workflow_step_runs.sources JSON` and `workflow_runs.acting_role`. There are no new
tables.

### 2. Capture at the few places that read protected data

`ToolContext` gets one small collector, `context.provenance.add(ref)`. It is written to
at existing choke points; the model cannot write to it.

- `search_knowledge` and knowledge-table tools: the ids of documents and tables
  actually returned.
- Memory: the ids injected by `PERSISTENT` context and returned by `recall_memories`.
- `IntegrationGateway.use` and `call_mcp`: the connection id.
- Workflow templating: `render()` reports which `steps.<id>` paths it read. The step
  inherits those upstream steps' sources (transitive, deduplicated).
- Agent step: the step's sources are the agent run's sources.
- Run input from a webhook or event: `external_input`.

Bounds: at most 500 references per run. On overflow, the run is marked
`sources_truncated`, which is treated as *unreadable by anyone but participants*
(fail closed).

### 3. Authorization-aware disclosure on read

One function, `can_read_sources(db, principal, sources) -> bool`, reuses existing
predicates:

| Reference | Check |
|---|---|
| knowledge_document / knowledge_table | `readable_documents_query(principal)`; a table is readable when its parent document is (`knowledge_tables.document_id`) |
| memory | `_visible_to_member(principal.user_id)`, so USER memories are owner-only |
| integration | `integration:use` (connections are organization-level) |
| workflow_step / agent_run | recursive on their `sources` |
| external_input | organization member |
| a source that no longer exists | **false** (fail closed) |

The ADR-0035 content rule becomes:
`participant(run, viewer) OR can_read_sources(viewer, run.sources)`.

Seeing the content then never tells the viewer anything they could not already read.
Outputs derived only from organization-level data stay visible to the organization, so
the operational value is kept. The check is done in bulk (one query per reference
type), never per row in Python loops over lists.

### 4. Publication sinks carry provenance forward

A task, an AGENT/ORG memory, an outbound email or webhook, or a knowledge upload
created by a run stores `source_run_id` (memory already has `source_run_id`) and adds
the run's sources summary to its audit event:

```
workflow.tool_executed / agent.tool_executed
  metadata: {tool, run_id, source_counts: {knowledge_document: 2, memory: 1}, restricted: true}
```

Publication is allowed, because the acting person may publish what they can read. It
is **attributable**: "which tasks were written from restricted sources" is one query.
An organization setting (`require_approval_to_publish_restricted`, default off) can
route such publications through the existing approval flow. This is a later option,
not part of the first change.

### 5. What is explicitly not done

- No content labels or classifiers on text, and no ML "sensitivity" detection.
- No re-encryption of outputs and no per-field keys.
- No policy engine. Checks are the existing SQL predicates.
- No retroactive backfill. Runs without `sources` fall back to participant-only
  (ADR-0035), which is fail closed.

## Consequences

- The platform can answer "why can this person see this output?" and "where did this
  text come from?" from data it already has, plus one JSON list.
- The overhead is a list append per protected read and one bulk authorization query
  per content read by a non-participant.
- Transitive recording is conservative. A step that *reads* a restricted output but
  doesn't *use* it is still marked. Over-restriction is the chosen failure mode.
