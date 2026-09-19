# AI Core (Phase 2)

The AI Core is the reusable, provider-independent AI infrastructure that every
future Noblen capability (agents, workflows, content, customer service, BusinessOS)
builds on. The application never depends on a vendor SDK directly.

## Architecture

```
             FastAPI endpoints (/api/v1/ai/*)
             auth · RBAC · tenant isolation · rate limit · usage recording
                                   │
                                   ▼
                            ┌──────────────┐
                            │  AI Gateway  │  provider/model selection,
                            │ (AIGateway)  │  normalization, timeouts,
                            └──────┬───────┘  retries, cost, logging
                                   │  (normalized types + errors only)
                            ┌──────▼───────┐
                            │  AIProvider  │  (interface / ABC)
                            └──────┬───────┘
                 ┌─────────────────┼──────────────────┐
                 ▼                 ▼                  ▼
        ┌────────────────┐ ┌──────────────┐ ┌──────────────────┐
        │ AnthropicProv. │ │ OpenAIProv.  │ │ MockProvider     │
        │ (Claude API)   │ │ (OpenAI API) │ │ (tests / offline)│
        └────────────────┘ └──────────────┘ └──────────────────┘
```

Rule: vendor SDK objects and exceptions never leave a provider adapter. Everything
flows through the normalized types in `app/ai/types.py` and the normalized errors in
`app/ai/errors.py`.

## Providers (`app/ai/providers/`)

| Provider   | Chat | Stream | Embeddings | Default model              |
|------------|------|--------|------------|----------------------------|
| Anthropic  | ✅   | ✅     | —          | `claude-sonnet-4-5`        |
| OpenAI     | ✅   | ✅     | ✅         | `gpt-4o-mini` / `text-embedding-3-small` |
| Mock       | ✅   | ✅     | ✅         | `mock-1` / `mock-embed-1`  |

SDKs are imported **lazily**, so importing the app requires no network or key, and
tests inject fake clients. Adapters map vendor exceptions into the normalized error
hierarchy.

## Gateway (`app/ai/gateway.py`)

`AIGateway` exposes `generate()`, `stream()`, and `embed()`. It handles:

- **Provider/model selection** — from the request, else configured defaults.
- **Normalization** — vendor responses → `GenerationResponse` / `EmbeddingResponse`;
  vendor stream events → `StreamChunk`.
- **Timeouts** — every external call is wrapped in `asyncio.wait_for`.
- **Retries** — bounded, exponential backoff, only for *retryable* errors.
- **Cost estimation** — via the pricing registry (labelled estimate, never a bill).
- **Attribution & logging** — request id, org, user, provider, model, tokens, latency,
  status. It is intentionally **DB-free**; persistence is the API/service layer's job.

The gateway is provided to endpoints through the `get_ai_gateway` FastAPI dependency,
which tests override with a mock-backed gateway.

## Configuration (`app/core/config.py`, `.env.example`)

| Setting | Default | Purpose |
|---------|---------|---------|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Provider secrets (backend only) |
| `AI_DEFAULT_PROVIDER` / `AI_DEFAULT_MODEL` | `anthropic` / `claude-sonnet-4-5` | Chat defaults |
| `AI_DEFAULT_EMBEDDING_PROVIDER` / `AI_DEFAULT_EMBEDDING_MODEL` | `openai` / `text-embedding-3-small` | Embedding defaults |
| `AI_REQUEST_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `AI_MAX_RETRIES` / `AI_RETRY_BASE_DELAY_SECONDS` | `2` / `0.5` | Retry policy |
| `AI_RATE_LIMIT_REQUESTS` / `AI_RATE_LIMIT_WINDOW_SECONDS` | `60` / `60` | Per-org rate limit |
| `AI_LOG_PROMPTS` | `false` | Privacy: keep prompt content out of logs |
| `AI_PRICING_OVERRIDES_JSON` | — | Override the pricing registry |

Automated tests never require a real key — they use the mock provider / fake clients.

## Usage metering (`app/models/ai_usage.py`, `app/services/ai_usage_service.py`)

Every request (success or failure) writes one tenant-scoped `ai_usage_records` row:
`organization_id`, `user_id`, `agent_id?`, `workflow_id?`, `provider`, `model`,
`operation`, `request_id`, token counts, `estimated_cost`, `estimated_cost_currency`,
`latency_ms`, `status`, `error_type?`, `created_at`. It stores **operational metadata
only** — never prompt/response content.

## Cost estimation (`app/ai/pricing.py`)

A configurable registry keyed by `"provider:model"` with input/output price per 1M
tokens, currency, and effective date. Costs are **estimates for visibility only,
never a provider invoice**. Unknown models yield `None` (the platform never invents a
price). Prices can be overridden via `AI_PRICING_OVERRIDES_JSON`.

## Errors (`app/ai/errors.py`)

`AIAuthenticationError`, `AIRateLimitError`, `AITimeoutError`,
`AIProviderUnavailableError`, `AIInvalidRequestError`, `AIQuotaError`,
`AIUnknownProviderError`, `AIUnknownError`. Each declares `retryable`. The API maps
them to safe HTTP responses (e.g. 429/503/504) without leaking provider internals.

## Retries & timeouts

Retryable: timeout, rate limit, provider-unavailable/network. Never retried: auth,
invalid request, quota. Bounded by `AI_MAX_RETRIES` with exponential backoff.

## Security

- Provider keys are backend-only; never logged, returned by an API, or exposed to the
  frontend.
- All AI endpoints require authentication and an AI permission (`ai:generate`,
  `ai:stream`, `ai:embed`, `ai:view_usage`), enforced server-side.
- Usage is tenant-isolated: Organization A can never read Organization B's usage.
- Clients cannot supply arbitrary provider configuration (provider is validated
  against an allow-list) and never supply credentials.
- Per-organization rate limiting protects the platform (per-plan limits later).

## API

| Method | Path | Permission | Notes |
|--------|------|------------|-------|
| POST | `/api/v1/ai/generate` | `ai:generate` | Normalized single-shot generation |
| POST | `/api/v1/ai/stream` | `ai:stream` | Server-Sent Events (`text/event-stream`) |
| POST | `/api/v1/ai/embed` | `ai:embed` | Embeddings |
| GET | `/api/v1/ai/usage` | `ai:view_usage` | Tenant-scoped usage records |

Streaming emits `data: {"type":"delta","delta":"..."}` events, a terminal
`{"type":"done", ...token counts...}`, or `{"type":"error", ...}`.

## Testing (`tests/ai/`)

- `test_providers.py` — Anthropic/OpenAI/mock normalization + failure mapping (fakes).
- `test_gateway.py` — selection, defaults, cost, streaming, timeout, retries.
- `test_ai_usage.py` — token/cost/attribution + org-scoped listing.
- `test_ai_api.py` — endpoints, auth, RBAC, **tenant isolation**, no-secrets-in-response.

No test makes a real API call.

## Not in this phase

RAG / vector DB, agents, workflows, WhatsApp, content/sales/customer-service
automation, billing — all later phases. Phase 2 is only the reusable AI Core.
