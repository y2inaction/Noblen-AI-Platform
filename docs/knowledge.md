# Knowledge + RAG (Phase 4)

The Knowledge Engine gives agents access to an organization's own documents through
retrieval-augmented generation, built on **PostgreSQL + pgvector** (the single
production vector store). It reuses the Phase 2 AI Gateway for all embeddings and the
Phase 3 Agent Runtime for retrieval.

## Architecture

```
Upload/Text → validate → extract → clean → chunk → embed (AI Gateway) → pgvector
                                                                          │
User → Agent Runtime → model → search_knowledge tool → authorize (agent's KBs)
                                                          → KnowledgeRetriever
                                                          → pgvector cosine search
                                                          → top-K chunks + citations
                                                          → tool result → model → answer
```

## Data model (`app/models/knowledge.py`)

| Table | Purpose |
|-------|---------|
| `knowledge_bases` | Org-owned collection; fixed embedding config (provider/model/dimension) |
| `knowledge_documents` | Document identity + ingestion lifecycle + source metadata |
| `document_chunks` | Cleaned, chunked text — the retrievable unit |
| `document_embeddings` | `vector(1536)` (pgvector) + retrieval metadata; HNSW cosine index |
| `agent_knowledge_sources` | Authorizes which KBs an agent may search (security boundary) |

`organization_id` and `knowledge_base_id` are denormalized onto chunks/embeddings so
the tenant-scoped vector search is a single indexed table scan.

## Ingestion pipeline (`app/knowledge/ingestion.py`)

`load → extract → clean → chunk → embed → store`. Supported types: **TXT, MD, PDF
(pypdf), DOCX (python-docx)**; unsupported types yield a clear validation error and
uploads are never executed. Failures leave the document in an actionable `FAILED`
state (never silently `READY`). Ingestion is idempotent by `(knowledge_base, checksum)`
and re-ingestion atomically replaces old chunks/embeddings only after new embeddings
succeed. Lifecycle: `PENDING → PROCESSING → READY | FAILED` (+ `ARCHIVED`).

## Embeddings & pgvector

All embeddings flow through the AI Gateway (`gateway.embed`) — never a vendor SDK in
the knowledge module. The vector column is fixed at `KNOWLEDGE_EMBEDDING_DIMENSION`
(default 1536, matching `text-embedding-3-small`); ingestion rejects any embedding of a
different dimension (`embedding_dimension_mismatch`). The column type
(`app/db/types.py::EmbeddingVector`) renders as `vector(dim)` on PostgreSQL and JSON on
SQLite, so the non-knowledge SQLite suites still build the shared schema. Cosine
similarity is computed **in PostgreSQL** with the `<=>` operator and an HNSW index —
vectors are never scored in Python.

## Retrieval (`app/knowledge/retrieval.py`)

`KnowledgeRetriever.search(...)` embeds the query, then runs a tenant-scoped cosine
search (`WHERE organization_id = :org [AND knowledge_base_id IN :kbs] ORDER BY
embedding <=> :q LIMIT top_k`), filters by `similarity_threshold`, and returns results
with stable **citations** (`document_name`, `chunk_index`, `page_number` when present —
never invented, similarity). Empty retrieval returns a clean "no relevant knowledge
found" — the system never fabricates sources.

## RAG via the agent (`search_knowledge` tool)

The Phase 3 ↔ Phase 4 bridge is the built-in `search_knowledge` tool (AUTO). The model
calls it; the **runtime** supplies `organization_id`/`agent_id` and resolves the agent's
authorized knowledge bases from `agent_knowledge_sources`. The model cannot widen scope
or reach another org via tool arguments. Retrieved passages are returned as **tool
data** (not system instructions) and are explicitly labelled untrusted — a document
saying "ignore previous instructions" can never change permissions, tools, or security
policy (prompt-injection defense).

## Security (spec §46)

- **Tenant isolation** — every retrieval query is org-scoped in SQL (proved by tests).
- **Agent authorization** — agents search only KBs in `agent_knowledge_sources`.
- **File security** — server-generated storage keys; no path traversal; files are data.
- **No secrets** — tools/documents never access application secrets.
- **RBAC** — `knowledge:view/create/update/delete/ingest/search/manage_sources`.

## API (`/api/v1`)

| Method | Path | Permission |
|--------|------|------------|
| POST/GET/PATCH/DELETE | `/knowledge-bases[...]` | `knowledge:create/view/update/delete` |
| POST | `/knowledge-bases/{id}/documents/text` · `/upload` | `knowledge:ingest` |
| GET/DELETE | `/knowledge-bases/{id}/documents`, `/documents/{id}` | `knowledge:view/delete` |
| POST | `/documents/{id}/ingest` (reprocess) | `knowledge:ingest` |
| POST | `/knowledge/search` | `knowledge:search` |
| POST/GET/DELETE | `/agents/{id}/knowledge-bases[...]` | `knowledge:manage_sources` / `agent:view` |

Ingestion is synchronous in Phase 4 (document create returns `READY`/`FAILED`); the
pipeline is modular so an async worker can drive the same stages later.

## Testing

Knowledge/RAG tests run against **real PostgreSQL + pgvector** (CI service
`pgvector/pgvector:pg16`; local dev via `KNOWLEDGE_TEST_DATABASE_URL`). If unavailable,
they skip so the SQLite suites are unaffected. Coverage: pipeline units
(extraction/cleaning/chunking), KB CRUD, ingestion (ready/failed/idempotent/re-ingest),
semantic search + citations, **tenant isolation**, RBAC, and end-to-end RAG through the
agent runtime with agent-authorization enforcement.

## Configuration

`KNOWLEDGE_EMBEDDING_PROVIDER/MODEL/DIMENSION`, `MAX_DOCUMENT_SIZE_MB`,
`MAX_DOCUMENT_TEXT_LENGTH`, `MAX_CHUNKS_PER_DOCUMENT`, `KNOWLEDGE_CHUNK_SIZE/OVERLAP`,
`KNOWLEDGE_DEFAULT_TOP_K`/`MAX_TOP_K`/`DEFAULT_SIMILARITY_THRESHOLD`,
`KNOWLEDGE_STORAGE_DIR`, `KNOWLEDGE_LOG_CONTENT` — see `.env.example`.

## Not in this phase
Async ingestion workers, web crawling (URL source is accepted but not ingested),
object-storage (S3) backend, reranking, and hybrid keyword+vector search — later phases.
