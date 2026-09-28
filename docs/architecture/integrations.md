# Integrations

**Status:** ✅ Implemented (3.0 M6): encrypted connections (credential references),
email, outbound webhooks, calendar (CalDAV), CRM (HubSpot), a remote MCP adapter,
and inbound webhook triggers for workflows. Code: `backend/app/integrations/`,
`backend/app/api/v1/integrations.py`, `backend/app/api/v1/hooks.py`.

No integration is simulated. Each provider speaks its real protocol. Tests run
against a local SMTP server and scripted HTTP services that check the requests.

## Connections (credential references)

An organization's administrators (`integration:manage`) create named connections:

| Provider | `config` (visible) | `secret` (write-only) |
|---|---|---|
| `SMTP` | `host`, `port`, `security` (`starttls` or `ssl`; `none` only in local dev), `from_email`, `from_name` | `username`, `password` |
| `WEBHOOK` | `url` (https, fixed by the admin) | `signing_secret`, `bearer_token` |
| `CALDAV` | `calendar_url` (the calendar collection) | `username`, `password` (use an app password) |
| `HUBSPOT` | — | `access_token` (private app; contact read/write scopes) |
| `MCP` | `url` (Streamable HTTP endpoint) | `bearer_token` |

- **Secrets never come back.** They are encrypted at rest with Fernet, never
  returned by the API (only masked hints such as `••••abcd`), and never handed to
  agents, tools, models, logs or the audit trail.
- **Tools refer to connections.** A tool names a connection, or uses the
  organization's only active connection of that kind. The run-bound
  `IntegrationGateway` decrypts the secret for the duration of the call.
- **Keys.** `INTEGRATIONS_ENCRYPTION_KEYS` holds Fernet keys, newest first. New
  secrets use the first key, and any listed key decrypts, so keys rotate without
  downtime. A key is required in production. Development derives one from `JWT_SECRET`.
- **Testing a connection.** `POST /integrations/{id}/test` checks the connection
  without doing anything: SMTP login, CalDAV `PROPFIND`, a HubSpot read, an MCP
  `initialize`, or a URL check for webhooks. It records `last_error`.
- **Disabling.** Setting `status: DISABLED` stops all use at once.

## Outbound safety

- **SSRF guard.** Every outbound connection, HTTP or SMTP, must resolve only to
  public addresses. Loopback, private, link-local (including cloud metadata),
  multicast and reserved ranges are refused. `INTEGRATIONS_ALLOW_PRIVATE_NETWORKS`
  lifts this for local development only.
- **Transport rules.**
  - HTTPS is required.
  - Credentials in URLs are rejected.
  - Redirects are never followed.
  - Timeouts (`INTEGRATIONS_HTTP_TIMEOUT_SECONDS`) and a response size cap
    (`INTEGRATIONS_MAX_RESPONSE_BYTES`) apply.
- **Known limit: DNS rebinding.** A hostname is resolved when it is checked and
  again when the client connects, so a DNS-rebinding attacker could race the two.
- **Untrusted data.** External data returned to a model is labelled untrusted.

## Tools and risk levels

| Tool | Risk | Default mode | Needs |
|---|---|---|---|
| `send_email` | HIGH | approval, always | an SMTP connection |
| `call_webhook` | HIGH | approval, always | a WEBHOOK connection |
| `create_calendar_event` | MEDIUM | approval | CALDAV |
| `list_calendar_events` | LOW | auto | CALDAV |
| `upsert_crm_contact`, `add_crm_note` | MEDIUM | approval | HUBSPOT |
| `find_crm_contacts` | LOW | auto | HUBSPOT |
| imported MCP tools | declared per tool (HIGH until changed) | approval until changed | MCP |

- **Permissions.** All integration tools require `integration:use` (MEMBER+) from
  the person the run acts for.
- **Workflows.** The built-in tools are also available as workflow tool steps; HIGH
  risk still waits for approval.
- **Scope.**
  - Calendar events are created without inviting attendees.
  - Recurring events are expanded by the server (CalDAV `expand`).
  - Reads cover at most 62 days.
- **Templates.**
  - Executive AI binds calendar and email.
  - Customer AI binds the CRM writes but not CRM search, because a customer-facing
    agent must not look up other customers.

## MCP adapter

1. An admin creates an `MCP` connection and calls `POST /integrations/{id}/tools/sync`.
   The adapter speaks MCP 2025-06-18 over Streamable HTTP (`initialize`,
   `tools/list`, `tools/call`; JSON or SSE responses) and imports each remote
   tool as an **organization-owned** catalogue tool named `mcp_<connection>_<tool>`.
   Other organizations never see or bind it.
2. Imported tools arrive **disabled, HIGH risk and approval-required**.
   `PATCH /integrations/{id}/tools/{tool_id}` enables a tool and declares its risk
   level and approval policy. HIGH-risk tools always need approval, whatever the
   binding says.
3. Agents bind imported tools like any other tool. Calls go through the same
   permission ceiling, approval, trace and audit. Results are bounded text or
   structured content, labelled untrusted.
4. Re-syncing updates descriptions and schemas. Tools the server no longer offers
   are disabled and cannot be re-enabled. Deleting the connection removes its tools.

MCP tools are available to agents only, not as workflow tool steps.

## Inbound webhooks (workflow trigger)

- **Setup.** A workflow with `"trigger": {"type": "webhook"}` gets a secret token
  when it is activated. The token is shown once, stored as a SHA-256 hash, and
  rotated on every activation.
- **Calling it.** External systems `POST /api/v1/hooks/workflows/{id}` with header
  `X-Noblen-Webhook-Token` and a JSON object body (at most
  `WEBHOOK_MAX_BODY_BYTES`). The body becomes the run's `input`.
- **Authority.** The run acts for the person who activated the workflow.
- **Hiding failures.** A wrong token, or an unknown, paused or non-webhook
  workflow, is always a 404. Requests are rate limited like the rest of the API.

## Audit

- **Connection changes:** `integration.created`, `integration.updated` (field names
  only), `integration.deleted`, `integration.mcp_synced` and
  `integration.mcp_tool_updated`.
- **External writes:** `integration.used`, with provider, action, agent and run.
  Read-only calls are not audited.
- **Never recorded:** secrets and message bodies.

## Not yet

- **OAuth-based connections.** Google and Microsoft Graph need OAuth flows;
  CalDAV and SMTP with app passwords are the supported path today.
- **Other channels and payments:** WhatsApp and other messaging channels, and
  Paystack billing.
- **More MCP:** MCP over stdio, MCP resources and prompts, and MCP tools in workflows.
- **Finer restrictions:** limiting which agents may use which connection. Today the
  tool binding and the connection name govern this.
