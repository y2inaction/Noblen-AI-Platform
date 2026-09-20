"""Noblen Knowledge + RAG Engine (Phase 4).

Ingestion (upload → validate → extract → clean → chunk → embed → store) and
tenant-scoped semantic retrieval over PostgreSQL + pgvector. Embeddings always go
through the Phase 2 AI Gateway; retrieval is exposed to agents only through the
permissioned `search_knowledge` tool.
"""
