# Model Gateway

**Status:** ✅ Implemented (Phase 2; fallback added in Noblen AI 3.0 M1). Full
reference: [`../ai-core.md`](../ai-core.md). Code: `backend/app/ai/`.

Noblen is **model-agnostic**. Application code builds a normalized
`GenerationRequest` and never imports a vendor SDK. `AIGateway` selects a
provider and model, applies timeouts and bounded retries, estimates cost from a
configurable pricing registry, normalizes errors, and logs every call. Usage is
persisted by the caller, which knows the attribution (`ai_usage_records`).

| Concept in the 3.0 spec | Implementation |
|---|---|
| ModelProvider | `AIProvider` (`app/ai/base.py`): Anthropic, OpenAI, Mock |
| Model / capability metadata | provider default models + pricing registry (`app/ai/pricing.py`) |
| ModelRequest / ModelResponse | `GenerationRequest` / `GenerationResponse` (`app/ai/types.py`) |
| ModelUsage / cost | token counts + `estimated_cost` → `ai_usage_records` |
| Routing | explicit provider/model → configured defaults |
| **Fallback (M1)** | ordered `provider:model` chain (below) |

## Fallback (M1)

A request may carry `fallbacks: ["openai:gpt-4o", "anthropic:"]`. Agent versions
set this through `configuration.fallback_models`. The platform may also set
`AI_FALLBACK_MODELS` (comma-separated), which is tried after the request's own
fallbacks.

- The primary is resolved exactly as before (`request.model` or the gateway default).
- Each candidate gets its own retries. An empty model uses the provider's default.
- **Invalid-request errors never fall back**: they describe the request, not
  the provider.
- A fallback naming an unconfigured provider is skipped, and the original upstream
  error is kept.
- A served fallback is recorded in `response.metadata["fallback_from"]`, which
  appears in the run trace.

## Gaps against the 3.0 spec (planned)

- Google Gemini and OpenAI-compatible self-hosted endpoints (vLLM, Ollama) need an
  adapter or a `base_url` option on the OpenAI provider.
- Capability metadata (tool use, vision, context window) as data, for
  capability-based routing.
- Preservation of provider-native assistant content (for example thinking blocks)
  for exact replay within tool loops.
