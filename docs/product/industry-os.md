# IndustryOS

**Status:** ⬜ Concept. No IndustryOS is being built yet. The platform is being
built so that they become possible.

```
IndustryOS
 ├── Industry Profile    ├── Domain Knowledge   ├── Agents
 ├── Workflows           ├── Tools              ├── Data Models
 ├── Templates           └── Governance
```

An IndustryOS is a **vertical configuration** of the platform, not a new
application: industry-specific agent templates, workflows, knowledge collections,
tool adapters and governance defaults (for example which tools are HIGH risk in that
sector).

## Candidate verticals and Noblen reference implementations

| IndustryOS | Reference in the Noblen ecosystem |
|---|---|
| AgricultureOS / ClimateOS / DevelopmentOS | CIDU |
| CommerceOS | ArewaMart |
| PropertyOS | Noblen Property |
| MediaOS | Noblen Media |
| EducationOS, HealthOS, GovernmentOS, FinanceOS | future |

## What the platform must provide (tracked in the architecture roadmap)

- A packaging format for templates (agents + workflows + knowledge + policies).
- Tool adapters that are registered per organization (integrations milestone).
- Domain data models introduced only when a vertical needs them. There will be
  no speculative schemas.
