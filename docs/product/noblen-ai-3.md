# Noblen AI 3.0

**NOBLEN AI: AI Workforce & Business Operating Systems.**

Noblen AI builds AI systems that understand organizations, execute business
workflows, and continuously improve how work gets done.

## What changed from the previous positioning

| Before | Noblen AI 3.0 |
|---|---|
| AI content + virtual assistant + automation + RAG | An AI **workforce** running on a business **operating system** |
| Separate AI features | One platform: agents, knowledge, automation and intelligence |
| Chat responses | Agents that act through tools, under permissions, with approval and audit |
| Single model vendor | Model-agnostic gateway (Anthropic, OpenAI, self-hosted, …) |

Noblen sells **capability** rather than hours, **outcomes** rather than AI novelty,
and **operating systems** rather than isolated features.

## What "an agent" means at Noblen

A chatbot reply is not an agent. A production Noblen agent has identity,
permissions, tools, knowledge, memory, execution, error handling, observability,
auditability and human escalation. Today the platform implements identity,
permissions, tools, knowledge (RAG), conversation and execution memory, execution,
error handling, observability, auditability, human approval and escalation. Long-term
user, agent and organizational memory come next. See
[`../architecture/overview.md`](../architecture/overview.md) for live status.

## Product layers

- **AI Workforce:** reference agents built on one framework ([workforce.md](workforce.md)).
- **BusinessOS:** the workforce plus knowledge, automation, intelligence and
  reporting for one organization ([businessos.md](businessos.md)).
- **IndustryOS:** vertical configurations of the same platform ([industry-os.md](industry-os.md)).
- **EnterpriseOS:** multi-unit governance, SSO and data residency (future).

## Principle for new features

Every major feature must answer this question: *What real organizational work does
this allow Noblen AI to execute or improve?* If there is no strong answer, the
feature is deferred.
