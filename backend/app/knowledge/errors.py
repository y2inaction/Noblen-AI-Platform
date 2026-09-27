"""Knowledge domain errors (AppError subclasses → structured HTTP responses)."""

from __future__ import annotations

from app.core.exceptions import AppError


class KnowledgeBaseNotFound(AppError):
    status_code = 404
    error_code = "knowledge_base_not_found"


class DocumentNotFound(AppError):
    status_code = 404
    error_code = "document_not_found"


class UnsupportedDocumentType(AppError):
    status_code = 422
    error_code = "unsupported_document_type"


class DocumentTooLarge(AppError):
    status_code = 413
    error_code = "document_too_large"


class DocumentExtractionError(AppError):
    status_code = 422
    error_code = "document_extraction_error"


class EmbeddingDimensionMismatch(AppError):
    status_code = 409
    error_code = "embedding_dimension_mismatch"


class IngestionError(AppError):
    status_code = 500
    error_code = "ingestion_error"


class KnowledgeBaseInactive(AppError):
    status_code = 409
    error_code = "knowledge_base_inactive"
