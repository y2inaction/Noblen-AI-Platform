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

Endpoints for agents, conversations, knowledge, leads, workflows, tasks,
integrations, and billing are added in their respective phases.
