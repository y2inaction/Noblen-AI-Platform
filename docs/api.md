# API

REST, versioned under `/api/v1`. Interactive OpenAPI docs are served at `/docs`
(Swagger UI) and the schema at `/openapi.json`.

## Conventions

- JSON request/response bodies validated by Pydantic v2.
- Errors are structured: `{ "error": { "code", "message", "details?" } }`.
- Auth: `Authorization: Bearer <access_token>`. The active organization is taken
  from the token's `org` claim, or overridden with the `X-Organization-Id` header
  (always re-validated against the caller's memberships).
- Verbs: `GET` (read), `POST` (create/action), `PATCH` (partial update),
  `DELETE` (remove). Pagination/filtering/sorting are added per-resource as
  collections land.

## Phase 1 endpoints

### Health
| Method | Path          | Auth | Notes                         |
|--------|---------------|------|-------------------------------|
| GET    | `/health`     | no   | Liveness                      |
| GET    | `/readiness`  | no   | Readiness (checks DB)         |
| GET    | `/`           | no   | Service metadata              |

### Auth — `/api/v1/auth`
| Method | Path         | Auth  | Notes                                    |
|--------|--------------|-------|------------------------------------------|
| POST   | `/register`  | no    | Create user + organization (owner=ADMIN) |
| POST   | `/login`     | no    | Returns access + refresh tokens          |
| POST   | `/refresh`   | token | Rotates refresh token                    |
| POST   | `/logout`    | no*   | Revokes the provided refresh token       |
| GET    | `/me`        | yes   | Current user + memberships               |

### Users — `/api/v1/users`
| Method | Path   | Auth | Notes                 |
|--------|--------|------|-----------------------|
| GET    | `/me`  | yes  | Current user profile  |
| PATCH  | `/me`  | yes  | Update own profile    |

### Organizations — `/api/v1/organizations`
| Method | Path                              | Permission            |
|--------|-----------------------------------|-----------------------|
| GET    | `/current`                        | `org:view`            |
| PATCH  | `/current`                        | `org:manage`          |
| GET    | `/current/members`                | `member:view`         |
| PATCH  | `/current/members/{id}/role`      | `member:update_role`  |

### AI Core — `/api/v1/ai` (Phase 2)
| Method | Path        | Permission        | Notes                              |
|--------|-------------|-------------------|------------------------------------|
| POST   | `/generate` | `ai:generate`     | Normalized single-shot generation  |
| POST   | `/stream`   | `ai:stream`       | Server-Sent Events stream          |
| POST   | `/embed`    | `ai:embed`        | Embeddings                         |
| GET    | `/usage`    | `ai:view_usage`   | Tenant-scoped AI usage records     |

See [`ai-core.md`](./ai-core.md) for request/response shapes, streaming event
format, cost estimation, and error semantics.

### Agents — `/api/v1/agents` (Phase 3)
| Method | Path | Permission |
|--------|------|------------|
| POST | `/agents` | `agent:create` |
| GET | `/agents` · `/agents/{id}` | `agent:view` |
| PATCH | `/agents/{id}` | `agent:update` |
| DELETE | `/agents/{id}` (archive) | `agent:delete` |
| POST | `/agents/{id}/activate` · `/pause` | `agent:operate` (AI Operator and above) |
| POST | `/agents/{id}/archive` | `agent:update` |
| POST/GET | `/agents/{id}/versions` (+ `/{version_id}`) | `agent:manage_versions` / `agent:view` |
| POST | `/agents/{id}/versions/{version_id}/activate` | `agent:manage_versions` |
| POST | `/agents/{id}/execute` | `agent:run` |

### Conversations — `/api/v1/conversations` (Phase 3)
| Method | Path | Permission |
|--------|------|------------|
| POST | `/conversations` | `conversation:create` |
| GET | `/conversations` · `/{id}` · `/{id}/messages` | `conversation:view` |
| POST | `/conversations/{id}/messages` | `conversation:write` |

Since the 3.0 security review, conversations are **private to their participants**
(the creator plus added participants). Other members, admins included, do not see
them in lists and get 404 on read, write, or `POST /agents/{id}/execute` with that
`conversation_id`. They can hold private user memories and restricted knowledge that
an agent retrieved for that person. Run traces (`/runs`) stay visible with
`run:view`; they record tool names and argument names, never values or content.

### Tools — `/api/v1` (Phase 3)
| Method | Path | Permission |
|--------|------|------------|
| GET | `/tools` · `/tools/{id}` | `tool:view` |
| GET | `/agents/{id}/tools` | `agent:view` |
| POST/DELETE | `/agents/{id}/tools` (bind/unbind) | `tool:manage` |

### Approvals — `/api/v1/approvals` (Phase 3)
| Method | Path | Permission |
|--------|------|------------|
| GET | `/approvals` · `/approvals/{id}` | `agent:approve_actions` |
| POST | `/approvals/{id}/approve` · `/reject` (optional `{"note"}`) | `agent:approve_actions` |
| POST | `/approvals/{id}/modify` `{"arguments", "note"}` | `agent:approve_actions` |

Approvals carry `restricted_publication` (Milestone 9): true when the request gates
an agent publication derived from restricted or unknown sources, which
`require_approval_to_publish_restricted` makes wait for approval. Approver
eligibility and payload confidentiality for these requests are not implemented yet.

Agent execution returns a normalized result whose `status` is `completed`,
`awaiting_approval` or `escalated`, with a `run_id` (and `escalation_reason` when
escalated). See [`architecture/agents.md`](./architecture/agents.md).

### Agent templates — `/api/v1/agent-templates` (Noblen AI 3.0, M2)
| Method | Path | Permission |
|--------|------|------------|
| GET | `/agent-templates` | `agent:view` |
| POST | `/agent-templates/{key}/instantiate` `{"name"?, "activate"?}` | `agent:create` + `tool:manage` + `agent:manage_versions` |

`POST /agents/{id}/execute` accepts `"background": true` → **202** `status: "queued"`
(run executed by the worker; poll `/runs/{run_id}`).

### Tasks & notifications — `/api/v1` (Noblen AI 3.0, M2)
| Method | Path | Permission |
|--------|------|------------|
| GET | `/tasks?status&open_only&mine&assignee_id` · `/tasks/{id}` | `task:view` |
| POST | `/tasks` · PATCH `/tasks/{id}` | `task:manage` |
| GET | `/notifications?unread_only` (caller's own) | any member |
| POST | `/notifications/{id}/read` · `/notifications/read-all` | any member (own only) |

`PATCH /organizations/current` accepts `require_independent_approval` (separation of duties).
It also accepts `require_approval_to_publish_restricted` (boolean, default `false`;
Milestone 9, ADR-0038), returned by `GET /organizations/current`. The setting is
enforced for agent publications (see Approvals above) and workflow tool steps
(see Workflows).

### Memory — `/api/v1/memories` (Noblen AI 3.0, M4)
| Method | Path | Permission |
|---|---|---|
| GET | `/memories?scope=&agent_id=&q=&limit=&offset=` | `memory:view` (own USER memories plus AGENT and ORGANIZATION) |
| POST | `/memories` `{scope, content, category?, agent_id?}` | own USER: `memory:write`; AGENT/ORGANIZATION: `memory:manage` |
| GET/PATCH/DELETE | `/memories/{id}` | as above; other people's USER memories are always 404 |
| DELETE | `/memories/mine` | `memory:write`: deletes all of the caller's USER memories |

Retention is set with `PATCH /organizations/current` `{"memory_retention_days": 90}`
(`org:manage`; `null` keeps memories until deleted). See
[`architecture/memory.md`](architecture/memory.md).

### Workflows — `/api/v1` (Noblen AI 3.0, M5)
| Method | Path | Permission |
|---|---|---|
| GET/POST | `/workflows` (`{name, description?, definition}`) | `workflow:view` / `workflow:manage` |
| GET/PATCH/DELETE | `/workflows/{id}` (DELETE archives) | `workflow:view` / `workflow:manage` |
| GET/POST | `/workflows/{id}/versions` (`{definition}`) | `workflow:view` / `workflow:manage` |
| POST | `/workflows/{id}/activate` (`{version_id?}`) · `/workflows/{id}/pause` | `workflow:manage` |
| POST | `/workflows/{id}/runs` (`{input, version_id?}`), returns **202** | `workflow:run` (`version_id` test runs: `workflow:manage`) |
| GET | `/workflow-runs?workflow_id=&status=` · `/workflow-runs/{id}` (with step trace) | `workflow:view`; content only for the run's person (below) |
| POST | `/workflow-runs/{id}/approve` · `/reject` (`{note?}`) | `agent:approve_actions` |
| POST | `/workflow-runs/{id}/cancel` | `agent:operate` |

Steps carry `restricted_publication` (Milestone 9): true when a tool step waits, or
waited, at approval because `require_approval_to_publish_restricted` is on and what
it consumes is restricted or unknown. It is decided before the tool runs; a step that
already required approval keeps its single decision. Approver eligibility and payload
confidentiality are not implemented yet.

**Run content (ADR-0035, ADR-0037).**
- Visible to everyone with the view permission: status, steps and their statuses,
  timings, error codes and the initiator.
- Content (`input`, `context`, step `output`, and `error` / `decision_note` text) is
  visible to the person the run acts for, and to anyone who can currently read every
  source the run recorded.
- Everyone else gets `null` for those fields, `content_withheld: true` and
  `content_withheld_reason`: `restricted_sources` (a source they cannot read) or
  `unknown_provenance` (missing, empty, truncated or unresolvable provenance, which
  fails closed). The response never names the source. Approvers
  (`agent:approve_actions`) still see the output of approval requests (approval
  steps, and tool steps waiting for or rejected at approval).
- The same rule covers `/runs` (`escalation_reason`, failed-tool `error`),
  `/operations/overview` (`recent_escalations[].reason`) and the `execution`
  payload of approval decisions.

Runs execute on the worker (`python -m app.agents.worker`). See
[`architecture/workflows.md`](architecture/workflows.md) for the definition format.

### Current access — `/api/v1/organizations/current/access` (Noblen AI 3.0, M7)
`GET` returns `{organization_id, organization_name, role_name, is_platform_admin,
permissions}` for the active organization (selected with `X-Organization-Id`). Any
member may call it. Clients use it to show only what the caller may do; the server
still checks every request. `GET /auth/me` memberships now include
`organization_name`.

### Integrations — `/api/v1/integrations` (Noblen AI 3.0, M6)
| Method | Path | Permission |
|---|---|---|
| GET | `/integrations` · `/integrations/{id}` | `integration:view` (secrets are never returned, only masked hints) |
| POST | `/integrations` (`{provider, name, config, secret}`) | `integration:manage` |
| PATCH/DELETE | `/integrations/{id}` (secret keys merge; `null` removes a key) | `integration:manage` |
| POST | `/integrations/{id}/test` → `{ok, error}` | `integration:manage` |
| GET | `/integrations/{id}/tools` (MCP) | `integration:view` |
| POST | `/integrations/{id}/tools/sync` (MCP) | `integration:manage` |
| PATCH | `/integrations/{id}/tools/{tool_id}` (`{enabled, risk_level, permission_mode}`) | `integration:manage` |

### Inbound webhooks — `/api/v1/hooks` (Noblen AI 3.0, M6)
`POST /hooks/workflows/{workflow_id}` with header `X-Noblen-Webhook-Token: <token>` and
a JSON object body returns **202** `{run_id, status}`. There is no user session. The
token is shown once by `POST /workflows/{id}/activate` for webhook-triggered workflows
(`webhook_token`, `webhook_path`). Every failure is a 404. See
[`architecture/integrations.md`](architecture/integrations.md).

### Runs & AI Operations — `/api/v1` (Noblen AI 3.0, M1)
| Method | Path | Permission |
|--------|------|------------|
| GET | `/runs?agent_id&status&limit&offset` | `run:view` |
| GET | `/runs/{id}` (with step trace) | `run:view` |
| GET | `/operations/overview?window_days=30` | `operations:view` |

### Knowledge + RAG — `/api/v1` (Phase 4)
| Method | Path | Permission |
|--------|------|------------|
| POST/GET/PATCH/DELETE | `/knowledge-bases` (+ `/{id}`) | `knowledge:create/view/update/delete` |
| POST | `/knowledge-bases/{id}/documents/text` · `/upload` | `knowledge:ingest` |
| GET | `/knowledge-bases/{id}/documents` · `/documents/{id}` | `knowledge:view` |
| DELETE | `/documents/{id}` | `knowledge:delete` |
| POST | `/documents/{id}/ingest` (reprocess) | `knowledge:ingest` |
| POST | `/knowledge/search` | `knowledge:search` |
| POST/GET/DELETE | `/agents/{id}/knowledge-bases` (+ `/{kb_id}`) | `knowledge:manage_sources` / `agent:view` |
| GET/PUT | `/knowledge-bases/{id}/access` (M3) | `knowledge:manage_access` |
| GET/PUT | `/documents/{id}/access` (M3) | `knowledge:manage_access` |
| GET | `/knowledge/tables?knowledge_base_id=&document_id=` (M3) | `knowledge:view` |
| POST | `/knowledge/tables/{id}/query` (M3) | `knowledge:search` |

Since M3, every knowledge endpoint returns only what the caller may read.
Anything else is a 404. Access bodies look like
`{"visibility": "RESTRICTED", "grants": [{"principal_type": "ROLE", "principal": "MANAGER"}]}`.
Knowledge-base visibility is `ORGANIZATION` or `RESTRICTED`; document visibility is
`INHERIT` or `RESTRICTED`.

See [`knowledge.md`](./knowledge.md) for the ingestion pipeline, pgvector retrieval,
citations, and the `search_knowledge` RAG tool.

Endpoints for leads, workflows, integrations, and billing are added in their
respective phases.
