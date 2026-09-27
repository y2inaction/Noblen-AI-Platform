# Noblen Knowledge Engine

**Status:** 🟡 Core implemented (Phase 4). Full reference:
[`../knowledge.md`](../knowledge.md). Code: `backend/app/knowledge/`.

```
Knowledge Source ─► Ingestion ─► Processing ─► Chunking ─► Embeddings (pgvector)
   ─► Retrieval ─► Context Assembly ─► Agent (search_knowledge tool, cited, untrusted data)
```

## What exists

- Knowledge bases → documents (PDF, DOCX, TXT/MD, CSV/XLSX, pasted text) → chunks →
  embeddings in pgvector with an HNSW index.
- Retrieval is scoped **in SQL** by organization, and for agents to the knowledge
  bases explicitly attached to that agent.
- Passages reach the model only as tool data, labelled untrusted
  (prompt-injection defence), with citations.
- Since M1, the `search_knowledge` tool also requires the initiating user to hold
  `knowledge:search`.

## Gaps against the 3.0 spec (planned, M3)

- **Finer-grained permissions:** document and collection ACLs by user and role.
  Today, access within an organization is governed by RBAC plus agent attachment.
  Filtering must happen inside the retrieval query, never after ranking.
- **Structured retrieval:** answering tabular and database questions with
  queries, alongside vector search.
- **More sources:** websites (URL ingestion is accepted but not implemented),
  databases, email and project records through integrations.
