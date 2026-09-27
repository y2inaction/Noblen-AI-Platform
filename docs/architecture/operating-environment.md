# Operating Environment (web UI)

**Status:** ✅ Implemented (3.0 M7). Code: `frontend/src/app/(app)/`,
`frontend/src/components/`, `frontend/src/lib/`. Built with Next.js 14 (App
Router), React and Tailwind. No other runtime dependencies were added.

The UI is where people supervise and direct their AI workforce. It uses only the
public API. Every permission is enforced by the server; the UI hides actions the
caller cannot take, as a convenience.

## Pages

| Page | What it shows and does | Needs |
|---|---|---|
| **Dashboard** `/dashboard` | Last 7 days: decisions waiting, runs and completion rate, escalation and failure rates, model spend and tokens, agents by status, tool calls and denials, recent runs and escalations, my open tasks | `operations:view`, `run:view`, `task:view` (each panel appears only if permitted) |
| **Workforce** `/workforce` | Agents (status, type, memory mode), pause and resume, ready-made agents (Executive AI, Customer AI) with what they can do and what is not available yet | `agent:view`; `agent:operate` to pause; `agent:create` to add |
| **Agent** `/workforce/{id}` | Configuration, recent runs, and "Give it a task" (run now or in the background) with the outcome: answer, waiting for approval, escalated or queued | `agent:run` to run |
| **Approvals** `/approvals` | One inbox for agent actions (tool, risk level, arguments; approve, reject, or edit arguments and approve, with a note) and workflow approval steps (approve or reject with a note) | `agent:approve_actions` |
| **Runs** `/runs` | Agent runs and workflow runs, filtered by status | `run:view`, `workflow:view` |
| **Run trace** `/runs/{id}` | Step-by-step trace (model calls, tools, approvals, memory, escalation) with tokens, latency and cost. The trace never contains prompt or response text | `run:view` |
| **Workflow run** `/workflow-runs/{id}` | Steps, attempts, decisions and notes, linked agent runs, outputs and input; cancel a queued or waiting run | `workflow:view`; `agent:operate` to cancel |
| **Workflows** `/workflows` | List, activate and pause, run now with JSON input, and create a draft from a JSON definition (with a working example). Webhook tokens are shown once, on activation | `workflow:view`, `workflow:run`, `workflow:manage` |
| **Tasks** `/tasks` | Open, mine or all; add a task; mark done. Tasks created by agents are labelled | `task:view`, `task:manage` |
| **Integrations** `/integrations` | Connections with visible settings and masked secret hints; test a connection; for MCP servers, sync tools, enable or disable each one and set its risk level | `integration:view`, `integration:manage` |

**Shell.**
- Permission-aware navigation, with a pending-approvals badge. The badge counts
  agent approvals only; workflow decisions appear in the inbox.
- An organization switcher, shown when the person belongs to several.
- A notifications menu, refreshed every 30 seconds, whose items link to the
  related approval, run or task.

**Sign-in.**
- `/login` signs in or creates an organization.
- It returns to the page that required sign-in, same-site paths only.

## Session and security

- **Tokens.**
  - Access and refresh tokens are kept in `sessionStorage`, so they last for one
    tab and are cleared when it closes.
  - A 401 triggers one refresh (`/auth/refresh`) and a retry. If that fails, the
    person is signed out.
  - Signing out revokes the refresh token on the server.
- **Organization.** The active organization is sent as `X-Organization-Id`.
  Permissions come from `GET /organizations/current/access`, added in M7.
- **Rendering.** API data is always rendered as text: tool arguments, model
  answers, notification bodies and step outputs. The UI never renders HTML
  from the API.
- **Content-Security-Policy with a per-request nonce** (`src/proxy.ts`):
  - `script-src 'self' 'nonce-…' 'strict-dynamic'`. There is no
    `'unsafe-inline'`: Next.js puts the nonce on its own scripts, so a script
    injected into a page does not run.
  - Styles are also nonce-bound. Connections are limited to the API origin.
  - `object-src 'none'`, `base-uri 'self'`, `form-action 'self'` and
    `frame-ancestors 'none'`.
  - A fresh nonce means pages render per request instead of statically.
- **Other headers** (`next.config.mjs`): `X-Frame-Options: DENY`, `nosniff`, a
  strict referrer policy and a restrictive permissions policy.
- **Verified in a browser:** an inline `<script>` injected into the server's
  HTML runs without the policy and is blocked, with a CSP violation, with it.

## Testing

- **CI:** `npm run lint`, `npm run typecheck` and `npm run build`.
- **`frontend/e2e/smoke.mjs`** drives the real UI in Chromium against a running
  backend, worker and frontend. The backend uses the built-in mock model, so no
  API keys are needed. The walkthrough:
  1. sign-up, and the redirect back to the requested page;
  2. hire Executive AI;
  3. a task that pauses for approval, then approval from the inbox with a note;
  4. the notification arrives;
  5. the run trace;
  6. add a task;
  7. create, activate and run a workflow, which the worker executes;
  8. approve its step, and check the task it created;
  9. dashboard;
  10. integrations;
  11. sign-out.

  The script fails on any failed API call or page error. It is not in CI because
  it needs the whole stack and a browser. See
  [development.md](../development.md#ui-smoke-test).

## Not yet

- **Editing:** authoring agents (instructions, tool bindings, versions),
  knowledge-base management, memory management, and connection create/edit forms.
  These are available through the API.
- **Workflow authoring:** a visual workflow builder. Definitions are JSON today.
- **Live updates:** runs and approvals refresh on navigation, not live.
- **Browser tests in CI:** automated browser tests for every page.
