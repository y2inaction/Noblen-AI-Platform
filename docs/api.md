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
