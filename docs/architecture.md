# Architecture

The authoritative, high-level design lives in the repository root
[`ARCHITECTURE.md`](../ARCHITECTURE.md). This document collects deeper notes that
grow phase by phase.

## Layering recap

```
API (FastAPI routers)  →  Schemas (Pydantic)  →  Services (business logic,
transactions, tenant + permission enforcement)  →  Models/Repositories
(SQLAlchemy)  →  Core (config, security, logging, AI gateway, tools)
```

- Routers stay thin; they call services.
- Services own the transaction boundary and always scope by `organization_id`.
- Core cross-cutting concerns are provider-agnostic (AI, billing, channels).

## Request lifecycle (Phase 1)

1. `RequestContextMiddleware` assigns a request id and binds logging context.
2. `SecureHeadersMiddleware` adds security headers.
3. `RateLimitMiddleware` applies a per-client sliding window (health probes exempt).
4. CORS is applied for the configured origins.
5. The route's dependencies authenticate the caller, resolve the active tenant,
   and enforce the required permission before the handler runs.
6. Errors are converted to structured JSON by typed exception handlers.

## Extensibility seams

| Concern     | Interface / seam                              | First impl        |
|-------------|-----------------------------------------------|-------------------|
| AI          | `AIService` → `Provider`                      | Anthropic, OpenAI |
| Payments    | `BillingProvider`                             | Paystack          |
| Channels    | `Channel`                                     | WEB, API          |
| Tools       | Tool registry (schema + permission + handler) | built-in tools    |
| Vector store| pgvector via SQLAlchemy                       | Postgres          |
