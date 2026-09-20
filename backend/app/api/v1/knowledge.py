"""Knowledge + RAG API: knowledge bases, documents, ingestion, search, and
agent↔knowledge-base authorization. All endpoints are tenant-scoped and RBAC-gated.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGateway, get_ai_gateway
from app.api.deps import TenantContext, require_permission
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.db.session import get_db
from app.knowledge import ingestion, service
from app.knowledge.retrieval import KnowledgeRetriever
from app.models.enums import DocumentSourceType
from app.rbac.permissions import Permission
from app.schemas.knowledge import (
    AgentKnowledgeBaseAttach,
    AgentKnowledgeSourceOut,
    DocumentListOut,
    DocumentOut,
    DocumentTextCreate,
    KnowledgeBaseCreate,
    KnowledgeBaseListOut,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
)

router = APIRouter(tags=["knowledge"])

_MAX_UPLOAD_BYTES = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024


# --------------------------------------------------------------------------- #
# Knowledge bases
# --------------------------------------------------------------------------- #
@router.post("/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201)
async def create_knowledge_base(
    body: KnowledgeBaseCreate,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseOut:
    kb = await service.create_knowledge_base(
        db,
        ctx.organization_id,
        ctx.user.id,
        name=body.name,
        description=body.description,
        configuration=body.configuration,
        metadata=body.metadata,
    )
    await db.commit()
    await db.refresh(kb)
    return KnowledgeBaseOut.model_validate(kb)


@router.get("/knowledge-bases", response_model=KnowledgeBaseListOut)
async def list_knowledge_bases(
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_VIEW)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeBaseListOut:
    items, total = await service.list_knowledge_bases(
        db, ctx.organization_id, limit=limit, offset=offset
    )
    return KnowledgeBaseListOut(
        items=[KnowledgeBaseOut.model_validate(k) for k in items], total=total
    )


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    kb_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseOut:
    kb = await service.get_knowledge_base(db, ctx.organization_id, kb_id)
    return KnowledgeBaseOut.model_validate(kb)


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: uuid.UUID,
    body: KnowledgeBaseUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseOut:
    kb = await service.update_knowledge_base(
        db, ctx.organization_id, kb_id, updates=body.model_dump(exclude_unset=True)
    )
    await db.commit()
    await db.refresh(kb)
    return KnowledgeBaseOut.model_validate(kb)


@router.delete("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
async def delete_knowledge_base(
    kb_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_DELETE)),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseOut:
    """Soft-delete: archive the knowledge base (documents are preserved)."""
    kb = await service.archive_knowledge_base(db, ctx.organization_id, kb_id)
    await db.commit()
    await db.refresh(kb)
    return KnowledgeBaseOut.model_validate(kb)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
async def _create_and_ingest(
    db: AsyncSession,
    ctx: TenantContext,
    gateway: AIGateway,
    kb_id: uuid.UUID,
    *,
    name: str,
    source_type: str,
    data: bytes,
    mime_type: str | None,
    filename: str | None,
) -> DocumentOut:
    document, created = await service.create_document(
        db,
        ctx.organization_id,
        kb_id,
        ctx.user.id,
        name=name,
        source_type=source_type,
        data=data,
        mime_type=mime_type,
        filename=filename,
    )
    if created:
        kb = await service.get_knowledge_base(db, ctx.organization_id, kb_id)
        await ingestion.ingest_document(
            db,
            organization_id=ctx.organization_id,
            document=document,
            knowledge_base=kb,
            gateway=gateway,
            user_id=ctx.user.id,
        )
    await db.commit()
    await db.refresh(document)
    return DocumentOut.model_validate(document)


@router.post("/knowledge-bases/{kb_id}/documents/text", response_model=DocumentOut, status_code=201)
async def create_text_document(
    kb_id: uuid.UUID,
    body: DocumentTextCreate,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_INGEST)),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    return await _create_and_ingest(
        db,
        ctx,
        gateway,
        kb_id,
        name=body.name,
        source_type=DocumentSourceType.TEXT.value,
        data=body.content.encode("utf-8"),
        mime_type="text/plain",
        filename=f"{body.name}.txt",
    )


@router.post(
    "/knowledge-bases/{kb_id}/documents/upload", response_model=DocumentOut, status_code=201
)
async def upload_document(
    kb_id: uuid.UUID,
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_INGEST)),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ValidationError(f"File exceeds {settings.MAX_DOCUMENT_SIZE_MB} MB limit.")
    # Never trust the client path — keep only the basename.
    raw_name = (file.filename or "upload").replace("\\", "/").split("/")[-1]
    return await _create_and_ingest(
        db,
        ctx,
        gateway,
        kb_id,
        name=raw_name,
        source_type=DocumentSourceType.UPLOAD.value,
        data=data,
        mime_type=file.content_type,
        filename=raw_name,
    )


@router.get("/knowledge-bases/{kb_id}/documents", response_model=DocumentListOut)
async def list_documents(
    kb_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_VIEW)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DocumentListOut:
    items, total = await service.list_documents(
        db, ctx.organization_id, kb_id, limit=limit, offset=offset
    )
    return DocumentListOut(items=[DocumentOut.model_validate(d) for d in items], total=total)


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    doc = await service.get_document(db, ctx.organization_id, document_id)
    return DocumentOut.model_validate(doc)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_document(db, ctx.organization_id, document_id)
    await db.commit()


@router.post("/documents/{document_id}/ingest", response_model=DocumentOut)
async def reingest_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_INGEST)),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    doc = await service.get_document(db, ctx.organization_id, document_id)
    kb = await service.get_knowledge_base(db, ctx.organization_id, doc.knowledge_base_id)
    await ingestion.ingest_document(
        db,
        organization_id=ctx.organization_id,
        document=doc,
        knowledge_base=kb,
        gateway=gateway,
        user_id=ctx.user.id,
        force=True,
    )
    await db.commit()
    await db.refresh(doc)
    return DocumentOut.model_validate(doc)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
@router.post("/knowledge/search", response_model=SearchResponse)
async def search_knowledge(
    body: SearchRequest,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_SEARCH)),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    results = await KnowledgeRetriever(gateway).search(
        db,
        organization_id=ctx.organization_id,
        query=body.query,
        knowledge_base_ids=body.knowledge_base_ids,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        user_id=ctx.user.id,
    )
    await db.commit()  # persist embedding usage
    return SearchResponse(
        query=body.query,
        count=len(results),
        results=[
            SearchResultOut(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                knowledge_base_id=r.knowledge_base_id,
                document_name=r.document_name,
                content=r.content,
                similarity=r.similarity,
                chunk_index=r.chunk_index,
                citation=r.citation,
            )
            for r in results
        ],
    )


# --------------------------------------------------------------------------- #
# Agent ↔ knowledge-base authorization
# --------------------------------------------------------------------------- #
@router.post(
    "/agents/{agent_id}/knowledge-bases", response_model=AgentKnowledgeSourceOut, status_code=201
)
async def attach_agent_knowledge_base(
    agent_id: uuid.UUID,
    body: AgentKnowledgeBaseAttach,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_MANAGE_SOURCES)),
    db: AsyncSession = Depends(get_db),
) -> AgentKnowledgeSourceOut:
    link = await service.attach_agent_knowledge_base(
        db, ctx.organization_id, agent_id, body.knowledge_base_id
    )
    await db.commit()
    await db.refresh(link)
    return AgentKnowledgeSourceOut.model_validate(link)


@router.get("/agents/{agent_id}/knowledge-bases", response_model=list[uuid.UUID])
async def list_agent_knowledge_bases(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[uuid.UUID]:
    return await service.list_agent_knowledge_base_ids(db, ctx.organization_id, agent_id)


@router.delete("/agents/{agent_id}/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_agent_knowledge_base(
    agent_id: uuid.UUID,
    kb_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_MANAGE_SOURCES)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.detach_agent_knowledge_base(db, ctx.organization_id, agent_id, kb_id)
    await db.commit()
