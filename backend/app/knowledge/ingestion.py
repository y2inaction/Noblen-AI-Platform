"""Deterministic ingestion pipeline: load → extract → clean → chunk → embed → store.

Modular by design so an async worker can later drive the same stages. On failure the
document is left in an actionable FAILED state (never silently READY). Re-ingestion
atomically replaces old chunks/embeddings only after new embeddings succeed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGateway
from app.ai.types import EmbeddingRequest
from app.core.config import settings
from app.core.logging import get_logger
from app.knowledge import chunking, cleaning, extraction
from app.knowledge.errors import EmbeddingDimensionMismatch
from app.knowledge.storage import DocumentStorage, get_document_storage
from app.models.enums import DocumentStatus
from app.models.knowledge import DocumentChunk, DocumentEmbedding, KnowledgeBase, KnowledgeDocument
from app.services import ai_usage_service

logger = get_logger("knowledge.ingestion")


async def ingest_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    document: KnowledgeDocument,
    knowledge_base: KnowledgeBase,
    gateway: AIGateway,
    storage: DocumentStorage | None = None,
    user_id: uuid.UUID | None = None,
    force: bool = False,
) -> KnowledgeDocument:
    """Run the pipeline for one document. Never raises for expected failures — it sets
    the document's FAILED state and returns it (the caller commits)."""
    if document.status == DocumentStatus.READY.value and not force:
        return document

    storage = storage or get_document_storage()
    document.status = DocumentStatus.PROCESSING.value
    document.error_message = None
    await db.flush()

    try:
        if not document.source_uri:
            raise ValueError("document has no stored source")
        raw = storage.load(document.source_uri)

        extracted = extraction.extract(
            raw,
            filename=(document.doc_metadata or {}).get("filename") or document.name,
            mime_type=document.mime_type,
        )
        text = cleaning.clean_text(extracted.text)
        if len(text) > settings.MAX_DOCUMENT_TEXT_LENGTH:
            text = text[: settings.MAX_DOCUMENT_TEXT_LENGTH]
        chunks = chunking.chunk_text(text)
        if not chunks:
            raise ValueError("no extractable text content")

        # Embed all chunks via the AI Gateway (never a vendor SDK directly).
        embed_response = await gateway.embed(
            EmbeddingRequest(
                texts=[c.content for c in chunks],
                provider=knowledge_base.embedding_provider,
                model=knowledge_base.embedding_model,
                organization_id=organization_id,
                user_id=user_id,
            )
        )
        if len(embed_response.vectors) != len(chunks):
            raise ValueError("embedding count did not match chunk count")
        for vec in embed_response.vectors:
            if len(vec) != knowledge_base.embedding_dimension:
                raise EmbeddingDimensionMismatch(
                    f"Embedding dimension {len(vec)} != knowledge base dimension "
                    f"{knowledge_base.embedding_dimension}."
                )

        await ai_usage_service.record_embedding(
            db,
            organization_id=organization_id,
            user_id=user_id,
            response=embed_response,
            knowledge_base_id=knowledge_base.id,
            document_id=document.id,
        )

        # Atomic replacement: only now remove old chunks (cascades to embeddings).
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))

        for chunk, vector in zip(chunks, embed_response.vectors, strict=True):
            row = DocumentChunk(
                organization_id=organization_id,
                document_id=document.id,
                knowledge_base_id=knowledge_base.id,
                chunk_index=chunk.index,
                content=chunk.content,
                token_count=chunk.token_count,
                character_count=chunk.character_count,
                chunk_metadata=chunk.metadata,
            )
            db.add(row)
            await db.flush()  # obtain chunk id
            db.add(
                DocumentEmbedding(
                    organization_id=organization_id,
                    chunk_id=row.id,
                    document_id=document.id,
                    knowledge_base_id=knowledge_base.id,
                    embedding_provider=embed_response.provider,
                    embedding_model=embed_response.model,
                    embedding_dimension=len(vector),
                    embedding=vector,
                )
            )

        document.chunk_count = len(chunks)
        document.version = (document.version or 1) + (1 if force else 0)
        document.status = DocumentStatus.READY.value
        await db.flush()
        logger.info(
            "document_ingested",
            organization_id=str(organization_id),
            knowledge_base_id=str(knowledge_base.id),
            document_id=str(document.id),
            chunk_count=len(chunks),
            embedding_model=embed_response.model,
            embedding_dimension=knowledge_base.embedding_dimension,
            status="ready",
        )
        return document
    except Exception as exc:  # noqa: BLE001 - failure must be recorded, not raised
        document.status = DocumentStatus.FAILED.value
        document.error_message = f"{type(exc).__name__}: {exc}"[:1000]
        await db.flush()
        logger.info(
            "document_ingestion_failed",
            organization_id=str(organization_id),
            document_id=str(document.id),
            error_type=type(exc).__name__,
            status="failed",
        )
        return document
