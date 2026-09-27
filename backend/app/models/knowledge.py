"""Knowledge + RAG models (tenant-scoped).

Separation of concerns:
  KnowledgeBase   — a named, org-owned collection with a fixed embedding config
  KnowledgeDocument — document identity + ingestion lifecycle + extracted-source metadata
  DocumentChunk   — cleaned, chunked text (the retrievable unit)
  DocumentEmbedding — the vector representation (pgvector) + retrieval metadata
  AgentKnowledgeSource — authorizes which KBs an agent may search (security boundary)

`organization_id`/`knowledge_base_id` are denormalized onto chunks/embeddings so the
tenant-scoped vector search is a single indexed table scan.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.db.types import EmbeddingVector
from app.models.enums import DocumentSourceType, DocumentStatus, KnowledgeBaseStatus

# Platform-wide embedding dimension (matches the configured model). The pgvector
# column is fixed at this size; ingestion rejects embeddings of another dimension.
EMBEDDING_DIM = settings.KNOWLEDGE_EMBEDDING_DIMENSION


class KnowledgeBase(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_kb_org_slug"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=KnowledgeBaseStatus.ACTIVE.value, nullable=False, index=True
    )
    embedding_provider: Mapped[str] = mapped_column(
        String(64), default=settings.KNOWLEDGE_EMBEDDING_PROVIDER, nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(
        String(128), default=settings.KNOWLEDGE_EMBEDDING_MODEL, nullable=False
    )
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=EMBEDDING_DIM, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    kb_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    documents: Mapped[list[KnowledgeDocument]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KnowledgeDocument(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "checksum", name="uq_document_kb_checksum"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(16), default=DocumentSourceType.TEXT.value, nullable=False
    )
    # Storage key / URI for the raw bytes (never a user-supplied filesystem path).
    source_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # sha256 hex
    status: Mapped[str] = mapped_column(
        String(16), default=DocumentStatus.PENDING.value, nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")
    embedding: Mapped[DocumentEmbedding | None] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", uselist=False
    )


class DocumentEmbedding(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "document_embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Denormalized for efficient tenant-scoped vector search.
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(EMBEDDING_DIM), nullable=False)

    chunk: Mapped[DocumentChunk] = relationship(back_populates="embedding")


class AgentKnowledgeSource(UUIDMixin, TimestampMixin, TenantMixin, Base):
    """Authorizes a specific agent to search a specific knowledge base."""

    __tablename__ = "agent_knowledge_sources"
    __table_args__ = (UniqueConstraint("agent_id", "knowledge_base_id", name="uq_agent_kb_source"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
