"""add knowledge and rag (phase 4)

Revision ID: ded89ed18034
Revises: 4d838a423ef5
Create Date: 2026-09-19 23:58:17.413908
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "ded89ed18034"
down_revision: str | None = "4d838a423ef5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Platform embedding dimension (matches KNOWLEDGE_EMBEDDING_DIMENSION default).
EMBEDDING_DIM = 1536


def upgrade() -> None:
    # pgvector is the single production vector store.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_bases",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("kb_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_kb_org_slug"),
    )
    op.create_index(op.f("ix_knowledge_bases_organization_id"), "knowledge_bases", ["organization_id"])
    op.create_index(op.f("ix_knowledge_bases_slug"), "knowledge_bases", ["slug"])
    op.create_index(op.f("ix_knowledge_bases_status"), "knowledge_bases", ["status"])

    op.create_table(
        "agent_knowledge_sources",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "knowledge_base_id", name="uq_agent_kb_source"),
    )
    op.create_index(op.f("ix_agent_knowledge_sources_agent_id"), "agent_knowledge_sources", ["agent_id"])
    op.create_index(
        op.f("ix_agent_knowledge_sources_knowledge_base_id"),
        "agent_knowledge_sources",
        ["knowledge_base_id"],
    )
    op.create_index(
        op.f("ix_agent_knowledge_sources_organization_id"),
        "agent_knowledge_sources",
        ["organization_id"],
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_uri", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("doc_metadata", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_base_id", "checksum", name="uq_document_kb_checksum"),
    )
    op.create_index(op.f("ix_knowledge_documents_checksum"), "knowledge_documents", ["checksum"])
    op.create_index(
        op.f("ix_knowledge_documents_knowledge_base_id"), "knowledge_documents", ["knowledge_base_id"]
    )
    op.create_index(
        op.f("ix_knowledge_documents_organization_id"), "knowledge_documents", ["organization_id"]
    )
    op.create_index(op.f("ix_knowledge_documents_status"), "knowledge_documents", ["status"])

    op.create_table(
        "document_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )
    op.create_index(op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"])
    op.create_index(
        op.f("ix_document_chunks_knowledge_base_id"), "document_chunks", ["knowledge_base_id"]
    )
    op.create_index(
        op.f("ix_document_chunks_organization_id"), "document_chunks", ["organization_id"]
    )

    op.create_table(
        "document_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_embeddings_chunk_id"), "document_embeddings", ["chunk_id"], unique=True)
    op.create_index(op.f("ix_document_embeddings_document_id"), "document_embeddings", ["document_id"])
    op.create_index(
        op.f("ix_document_embeddings_knowledge_base_id"), "document_embeddings", ["knowledge_base_id"]
    )
    op.create_index(
        op.f("ix_document_embeddings_organization_id"), "document_embeddings", ["organization_id"]
    )
    # HNSW index for cosine similarity (pgvector >= 0.5). Cheap to build at low row
    # counts and native to PostgreSQL retrieval.
    op.create_index(
        "ix_document_embeddings_vector_hnsw",
        "document_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # AI usage attribution for knowledge activity (additive, nullable).
    op.add_column("ai_usage_records", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.add_column("ai_usage_records", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_ai_usage_records_document_id"), "ai_usage_records", ["document_id"]
    )
    op.create_index(
        op.f("ix_ai_usage_records_knowledge_base_id"), "ai_usage_records", ["knowledge_base_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_usage_records_knowledge_base_id"), table_name="ai_usage_records")
    op.drop_index(op.f("ix_ai_usage_records_document_id"), table_name="ai_usage_records")
    op.drop_column("ai_usage_records", "document_id")
    op.drop_column("ai_usage_records", "knowledge_base_id")

    op.drop_index("ix_document_embeddings_vector_hnsw", table_name="document_embeddings")
    op.drop_index(op.f("ix_document_embeddings_organization_id"), table_name="document_embeddings")
    op.drop_index(op.f("ix_document_embeddings_knowledge_base_id"), table_name="document_embeddings")
    op.drop_index(op.f("ix_document_embeddings_document_id"), table_name="document_embeddings")
    op.drop_index(op.f("ix_document_embeddings_chunk_id"), table_name="document_embeddings")
    op.drop_table("document_embeddings")

    op.drop_index(op.f("ix_document_chunks_organization_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_knowledge_base_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index(op.f("ix_knowledge_documents_status"), table_name="knowledge_documents")
    op.drop_index(op.f("ix_knowledge_documents_organization_id"), table_name="knowledge_documents")
    op.drop_index(op.f("ix_knowledge_documents_knowledge_base_id"), table_name="knowledge_documents")
    op.drop_index(op.f("ix_knowledge_documents_checksum"), table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

    op.drop_index(
        op.f("ix_agent_knowledge_sources_organization_id"), table_name="agent_knowledge_sources"
    )
    op.drop_index(
        op.f("ix_agent_knowledge_sources_knowledge_base_id"), table_name="agent_knowledge_sources"
    )
    op.drop_index(op.f("ix_agent_knowledge_sources_agent_id"), table_name="agent_knowledge_sources")
    op.drop_table("agent_knowledge_sources")

    op.drop_index(op.f("ix_knowledge_bases_status"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_slug"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_organization_id"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
