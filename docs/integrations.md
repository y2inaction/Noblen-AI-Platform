# Integrations (original design)

> **Implemented in Noblen AI 3.0 M6:** encrypted per-organization connections, SMTP
> email, outbound webhooks, CalDAV calendars, HubSpot CRM, a remote MCP adapter and
> inbound workflow webhooks. See [`architecture/integrations.md`](architecture/integrations.md).
> Not implemented yet: WhatsApp and other channels, Paystack billing, and OAuth-based
> Google/Microsoft connections. The `GOOGLE_*`, `WHATSAPP_*` and `PAYSTACK_*` variables
> below are placeholders for that work.

Integrations sit behind interfaces so providers can be added without touching
business logic. **No fake integrations**: where credentials are unavailable, we
ship the interface plus a mock/test provider, document the required credentials,
and continue.

## Channel abstraction
Initial: `WEB`, `API`. Prepared for `WHATSAPP`, `EMAIL`, `INSTAGRAM`, `FACEBOOK`,
`SMS`.

## Providers & required credentials
| Integration      | Env vars (see `.env.example`)                                   |
|------------------|-----------------------------------------------------------------|
| AI — Anthropic   | `ANTHROPIC_API_KEY`                                             |
| AI — OpenAI      | `OPENAI_API_KEY`                                               |
| Payments — Paystack | `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`                 |
| Google Calendar  | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`                     |
| WhatsApp Cloud   | `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` |
| Email (SMTP)     | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` |

## Data model
Implemented as `integration_connections` (secrets encrypted at rest) and
`integration_tools` (imported MCP tools); inbound webhooks are a workflow trigger.

## Paystack (billing) scope
Customer creation, subscription, payment verification, webhook handling, and
transaction records — behind a `BillingProvider` interface so other providers can
be added later. Plans and prices are configurable data, not hard-coded.

## Human-in-the-loop
Outward-facing integration actions (send email/WhatsApp, publish, payment) default
to `APPROVAL_REQUIRED`.
