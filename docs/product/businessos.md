# BusinessOS

**Status:** ⬜ Concept. It is built from the platform components as they land.

```
BusinessOS
├── Executive AI · Sales AI · Marketing AI · Customer AI
├── Operations AI · Finance Operations AI · Research AI
├── Knowledge · Automation · Intelligence · Reporting
```

BusinessOS is one organization's configured workforce plus the shared context
those agents operate in. In platform terms:

- **Agents:** versioned `Agent` configurations in the organization (implemented).
- **Shared context:** knowledge bases granted per agent (implemented) and
  organizational memory (planned), so that each agent sees only what it should.
- **Automation:** workflows that chain agents, tools and approvals (planned).
- **Intelligence and reporting:** built on the run trace, usage ledger and
  operations overview (foundations implemented).
- **Governance:** RBAC, AI Operators, approvals and audit (implemented).

No BusinessOS-specific code is planned. It should be deliverable as a packaged
configuration (agent templates + workflows + knowledge collections).
