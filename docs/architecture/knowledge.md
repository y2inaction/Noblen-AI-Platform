# Noblen Knowledge Engine

**Status:** 🟡 Core implemented (Phase 4), with access control and structured
retrieval (3.0 M3). Full reference:
[`../knowledge.md`](../knowledge.md). Code: `backend/app/knowledge/`.

```
Knowledge Source ─► Ingestion ─► Processing ─► Chunking ─► Embeddings (pgvector)
   ─► Retrieval ─► Context Assembly ─► Agent (search_knowledge tool, cited, untrusted data)
```

## What exists

- Knowledge bases → documents (PDF, DOCX, TXT/MD, CSV/XLSX, pasted text) → chunks →
  embeddings in pgvector with an HNSW index. (CSV/XLSX support arrived in M3;
  earlier versions of this page listed it before it existed.)
- Retrieval is scoped **in SQL** by organization, and for agents to the knowledge
  bases explicitly attached to that agent.
- Passages reach the model only as tool data, labelled untrusted
  (prompt-injection defence), with citations.
- Since M1, the `search_knowledge` tool also requires the initiating user to hold
  `knowledge:search`.

## Access control (M3)

- **Visibility.** A knowledge base is `ORGANIZATION` (every member) or
  `RESTRICTED` (its creator plus explicit grants). A document is `INHERIT` (the
  base decides) or `RESTRICTED` (its creator plus explicit grants, and the base
  must also be readable).
- **Grants** name a user (an active member) or a role (`MEMBER`, `MANAGER`, …).
  They are managed with `PUT /knowledge-bases/{id}/access` and
  `PUT /documents/{id}/access` (`knowledge:manage_access`, and the caller must be
  able to read the resource). Changes are audited as `knowledge.access_changed`.
- **Admins** hold `knowledge:read_all` and read everything in their organization.
  Platform admins are treated the same.
- **Enforced in the query.** `app/knowledge/access.py` builds SQL predicates that
  every listing, fetch, semantic search and table query adds to its `WHERE`
  clause. For search this happens before `ORDER BY … LIMIT top_k`, so restricted
  chunks never take a slot from readable ones and are never scored for the reader.
- **Agents read as their initiator.** The principal for `search_knowledge` and
  the table tools is the user who started the run, resolved from the database on
  every call. Agent attachment still applies on top: an agent sees the
  intersection of its attached bases and what its initiator may read.
- Only `ACTIVE` knowledge bases are searchable; archived and paused ones are not.
- The duplicate-upload check does not reveal a document the uploader cannot read
  (it returns a generic 409).

## Structured retrieval (M3)

CSV and XLSX documents are also stored as **tables**: each sheet's header row
names the columns, and each column is typed `number` (every non-empty value is a
number) or `text`. Rows are JSON in `knowledge_table_rows`. The same rows are also
rendered as text and embedded, so they stay findable by semantic search.

Tables are queried with a declarative spec, never with SQL written by a user or a
model:

```json
{"filters": [{"column": "City", "op": "eq", "value": "Kano"}],
 "aggregate": {"op": "sum", "column": "Amount"},
 "group_by": null, "order_by": null, "columns": null, "limit": 50}
```

- Filter ops: `eq ne gt gte lt lte contains is_null not_null`. Range ops need a
  numeric column; `contains` (case-insensitive) needs a text column.
- One aggregate: `count sum avg min max`, optionally with `group_by` (groups are
  ordered by the metric, largest first).
- Column names are checked against the table schema and values are bound
  parameters. The query runs in the database through JSON path expressions, on
  PostgreSQL and SQLite alike.
- Results are capped at `KNOWLEDGE_MAX_QUERY_ROWS` and report `truncated`.
- Tables follow the access rules of their document and knowledge base.

Agents use `list_data_tables` and `query_data_table`; people use
`GET /knowledge/tables` and `POST /knowledge/tables/{id}/query`.

## Remaining gaps

- **Relational sources:** querying external databases through integrations.
- **More sources:** websites (URL ingestion is accepted but not implemented),
  email and project records through integrations.
- **Joins across tables** and multiple aggregates per query.
