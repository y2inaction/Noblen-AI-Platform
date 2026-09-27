"""Knowledge base + document CRUD and agent-knowledge authorization (tenant-scoped)."""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import get_agent
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.utils import slugify, unique_suffix
from app.db.tenant import tenant_scoped
from app.knowledge.errors import (
    DocumentNotFound,
    DocumentTooLarge,
    KnowledgeBaseNotFound,
)
from app.knowledge.storage import DocumentStorage, get_document_storage
from app.models.enums import DocumentStatus, KnowledgeBaseStatus
from app.models.knowledge import (
    AgentKnowledgeSource,
    KnowledgeBase,
    KnowledgeDocument,
)


# --------------------------------------------------------------------------- #
# Knowledge bases
# --------------------------------------------------------------------------- #
async def _unique_kb_slug(db: AsyncSession, organization_id: uuid.UUID, name: str) -> str:
    base = slugify(name)
    slug = base
    while (
        await db.execute(
            tenant_scoped(select(KnowledgeBase.id), KnowledgeBase, organization_id).where(
                KnowledgeBase.slug == slug
            )
        )
    ).first():
        slug = f"{base}-{unique_suffix()}"
    return slug


async def create_knowledge_base(
    db: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    name: str,
    description: str | None = None,
    configuration: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> KnowledgeBase:
    kb = KnowledgeBase(
        organization_id=organization_id,
        created_by=created_by,
        name=name,
        slug=await _unique_kb_slug(db, organization_id, name),
        description=description,
        status=KnowledgeBaseStatus.ACTIVE.value,
        embedding_provider=settings.KNOWLEDGE_EMBEDDING_PROVIDER,
        embedding_model=settings.KNOWLEDGE_EMBEDDING_MODEL,
        embedding_dimension=settings.KNOWLEDGE_EMBEDDING_DIMENSION,
        configuration=configuration or {},
        kb_metadata=metadata or {},
    )
    db.add(kb)
    await db.flush()
    return kb


async def get_knowledge_base(
    db: AsyncSession, organization_id: uuid.UUID, kb_id: uuid.UUID
) -> KnowledgeBase:
    kb = (
        await db.execute(
            tenant_scoped(select(KnowledgeBase), KnowledgeBase, organization_id).where(
                KnowledgeBase.id == kb_id
            )
        )
    ).scalar_one_or_none()
    if kb is None:
        raise KnowledgeBaseNotFound("Knowledge base not found.")
    return kb


async def list_knowledge_bases(
    db: AsyncSession, organization_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> tuple[list[KnowledgeBase], int]:
    total = (
        await db.execute(
            tenant_scoped(select(func.count(KnowledgeBase.id)), KnowledgeBase, organization_id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                tenant_scoped(select(KnowledgeBase), KnowledgeBase, organization_id)
                .order_by(KnowledgeBase.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def update_knowledge_base(
    db: AsyncSession,
    organization_id: uuid.UUID,
    kb_id: uuid.UUID,
    *,
    updates: dict[str, Any],
) -> KnowledgeBase:
    kb = await get_knowledge_base(db, organization_id, kb_id)
    allowed = {"name", "description", "status", "configuration"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(kb, key, value)
    if updates.get("metadata") is not None:
        kb.kb_metadata = updates["metadata"]
    await db.flush()
    return kb


async def archive_knowledge_base(
    db: AsyncSession, organization_id: uuid.UUID, kb_id: uuid.UUID
) -> KnowledgeBase:
    kb = await get_knowledge_base(db, organization_id, kb_id)
    kb.status = KnowledgeBaseStatus.ARCHIVED.value
    await db.flush()
    return kb


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
async def create_document(
    db: AsyncSession,
    organization_id: uuid.UUID,
    kb_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    name: str,
    source_type: str,
    data: bytes,
    mime_type: str | None = None,
    filename: str | None = None,
    storage: DocumentStorage | None = None,
) -> tuple[KnowledgeDocument, bool]:
    """Create (or return existing, idempotent) a document. Returns (document, created)."""
    kb = await get_knowledge_base(db, organization_id, kb_id)

    max_bytes = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise DocumentTooLarge(f"Document exceeds {settings.MAX_DOCUMENT_SIZE_MB} MB limit.")

    checksum = hashlib.sha256(data).hexdigest()
    existing = (
        await db.execute(
            tenant_scoped(select(KnowledgeDocument), KnowledgeDocument, organization_id).where(
                KnowledgeDocument.knowledge_base_id == kb_id,
                KnowledgeDocument.checksum == checksum,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Idempotent: same bytes in the same KB → return the existing document.
        return existing, False

    storage = storage or get_document_storage()
    suffix = f".{filename.rsplit('.', 1)[-1]}" if filename and "." in filename else ""
    key = storage.save(organization_id, data, suffix=suffix)

    document = KnowledgeDocument(
        organization_id=organization_id,
        knowledge_base_id=kb.id,
        created_by=created_by,
        name=name,
        source_type=source_type,
        source_uri=key,
        mime_type=mime_type,
        file_size=len(data),
        checksum=checksum,
        status=DocumentStatus.PENDING.value,
        doc_metadata={"filename": filename} if filename else {},
    )
    db.add(document)
    await db.flush()
    return document, True


async def get_document(
    db: AsyncSession, organization_id: uuid.UUID, document_id: uuid.UUID
) -> KnowledgeDocument:
    doc = (
        await db.execute(
            tenant_scoped(select(KnowledgeDocument), KnowledgeDocument, organization_id).where(
                KnowledgeDocument.id == document_id
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise DocumentNotFound("Document not found.")
    return doc


async def list_documents(
    db: AsyncSession,
    organization_id: uuid.UUID,
    kb_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[KnowledgeDocument], int]:
    await get_knowledge_base(db, organization_id, kb_id)  # tenant + existence check
    base = tenant_scoped(select(KnowledgeDocument), KnowledgeDocument, organization_id).where(
        KnowledgeDocument.knowledge_base_id == kb_id
    )
    count = tenant_scoped(
        select(func.count(KnowledgeDocument.id)), KnowledgeDocument, organization_id
    ).where(KnowledgeDocument.knowledge_base_id == kb_id)
    total = (await db.execute(count)).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(KnowledgeDocument.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def delete_document(
    db: AsyncSession, organization_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    doc = await get_document(db, organization_id, document_id)
    storage = get_document_storage()
    if doc.source_uri:
        # Best-effort blob cleanup; DB removal is authoritative.
        with contextlib.suppress(Exception):
            storage.delete(doc.source_uri)
    await db.delete(doc)  # cascades to chunks + embeddings
    await db.flush()


# --------------------------------------------------------------------------- #
# Agent ↔ knowledge-base authorization
# --------------------------------------------------------------------------- #
async def attach_agent_knowledge_base(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, kb_id: uuid.UUID
) -> AgentKnowledgeSource:
    await get_agent(db, organization_id, agent_id)  # tenant + existence
    await get_knowledge_base(db, organization_id, kb_id)  # tenant + existence
    existing = (
        await db.execute(
            select(AgentKnowledgeSource).where(
                AgentKnowledgeSource.agent_id == agent_id,
                AgentKnowledgeSource.knowledge_base_id == kb_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Knowledge base is already attached to this agent.")
    link = AgentKnowledgeSource(
        organization_id=organization_id, agent_id=agent_id, knowledge_base_id=kb_id
    )
    db.add(link)
    await db.flush()
    return link


async def list_agent_knowledge_base_ids(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = (
        (
            await db.execute(
                tenant_scoped(
                    select(AgentKnowledgeSource.knowledge_base_id),
                    AgentKnowledgeSource,
                    organization_id,
                ).where(AgentKnowledgeSource.agent_id == agent_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def detach_agent_knowledge_base(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, kb_id: uuid.UUID
) -> None:
    link = (
        await db.execute(
            tenant_scoped(
                select(AgentKnowledgeSource), AgentKnowledgeSource, organization_id
            ).where(
                AgentKnowledgeSource.agent_id == agent_id,
                AgentKnowledgeSource.knowledge_base_id == kb_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise DocumentNotFound("Knowledge base is not attached to this agent.")
    await db.delete(link)
    await db.flush()
