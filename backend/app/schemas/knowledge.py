"""Knowledge + RAG schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    configuration: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseListOut(BaseModel):
    items: list[KnowledgeBaseOut]
    total: int


class DocumentTextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    source_type: str
    mime_type: str | None
    file_size: int
    checksum: str
    status: str
    version: int
    chunk_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class SearchResultOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_name: str
    content: str
    similarity: float
    chunk_index: int
    citation: dict[str, Any]


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchResultOut]


class AgentKnowledgeBaseAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: uuid.UUID


class AgentKnowledgeSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    knowledge_base_id: uuid.UUID
