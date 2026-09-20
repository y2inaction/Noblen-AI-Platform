"""Tenant-scoped semantic retrieval over pgvector.

Every query is constrained by ``organization_id`` at the SQL level, and optionally by
an explicit set of knowledge_base_ids. Similarity is computed in PostgreSQL with the
pgvector cosine operator (``<=>``) — vectors are never pulled into Python for scoring.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, bindparam, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGateway
from app.ai.types import EmbeddingRequest
from app.core.config import settings
from app.core.logging import get_logger
from app.models.knowledge import DocumentChunk, DocumentEmbedding, KnowledgeDocument
from app.services import ai_usage_service

logger = get_logger("knowledge.retrieval")


@dataclass
class SearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_name: str
    content: str
    similarity: float
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    citation: dict[str, Any] = field(default_factory=dict)


def _build_citation(index: int, row: Any, similarity: float) -> dict[str, Any]:
    page = (row.chunk_metadata or {}).get("page_number")
    label = f"[{index}] {row.document_name} — chunk {row.chunk_index}"
    if page is not None:
        label += f" — page {page}"
    return {
        "index": index,
        "label": label,
        "knowledge_base_id": str(row.knowledge_base_id),
        "document_id": str(row.document_id),
        "chunk_id": str(row.id),
        "document_name": row.document_name,
        "chunk_index": row.chunk_index,
        "page_number": page,
        "similarity": round(similarity, 6),
    }


class KnowledgeRetriever:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def search(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        query: str,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        top_k: int | None = None,
        similarity_threshold: float | None = None,
        user_id: uuid.UUID | None = None,
    ) -> list[SearchResult]:
        from pgvector.sqlalchemy import Vector  # pg-only; retrieval runs on PostgreSQL

        top_k = min(top_k or settings.KNOWLEDGE_DEFAULT_TOP_K, settings.KNOWLEDGE_MAX_TOP_K)
        threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else settings.KNOWLEDGE_DEFAULT_SIMILARITY_THRESHOLD
        )

        started = time.perf_counter()
        # Embed the query with the platform embedding config (matches ingestion).
        embed_response = await self._gateway.embed(
            EmbeddingRequest(
                texts=[query],
                provider=settings.KNOWLEDGE_EMBEDDING_PROVIDER,
                model=settings.KNOWLEDGE_EMBEDDING_MODEL,
                organization_id=organization_id,
                user_id=user_id,
            )
        )
        await ai_usage_service.record_embedding(
            db, organization_id=organization_id, user_id=user_id, response=embed_response
        )
        qvec = embed_response.vectors[0]
        dim = settings.KNOWLEDGE_EMBEDDING_DIMENSION

        distance = DocumentEmbedding.embedding.op("<=>", return_type=Float)(
            bindparam("qvec", value=qvec, type_=Vector(dim))
        )
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.knowledge_base_id,
                DocumentChunk.chunk_index,
                DocumentChunk.content,
                DocumentChunk.chunk_metadata,
                KnowledgeDocument.name.label("document_name"),
                distance.label("distance"),
            )
            .join(DocumentEmbedding, DocumentEmbedding.chunk_id == DocumentChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
            # MANDATORY tenant scope — never retrieve another org's vectors.
            .where(DocumentEmbedding.organization_id == organization_id)
            .order_by(distance)
            .limit(top_k)
        )
        if knowledge_base_ids:
            stmt = stmt.where(DocumentEmbedding.knowledge_base_id.in_(knowledge_base_ids))

        rows = (await db.execute(stmt)).all()
        results: list[SearchResult] = []
        for row in rows:
            similarity = 1.0 - float(row.distance)
            if similarity < threshold:
                continue
            idx = len(results) + 1
            results.append(
                SearchResult(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    knowledge_base_id=row.knowledge_base_id,
                    document_name=row.document_name,
                    content=row.content,
                    similarity=similarity,
                    chunk_index=row.chunk_index,
                    metadata=row.chunk_metadata or {},
                    citation=_build_citation(idx, row, similarity),
                )
            )
        logger.info(
            "knowledge_search",
            organization_id=str(organization_id),
            knowledge_base_ids=[str(k) for k in (knowledge_base_ids or [])],
            top_k=top_k,
            results=len(results),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return results
