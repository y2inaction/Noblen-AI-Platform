"""Document storage abstraction.

`LocalDocumentStorage` writes raw bytes under a per-organization directory with a
server-generated key — user-supplied filenames never become filesystem paths, and
path traversal is impossible. The interface is designed so an S3/object-store
backend can replace it later without touching the ingestion engine.
"""

from __future__ import annotations

import abc
import uuid
from pathlib import Path

from app.core.config import settings


class DocumentStorage(abc.ABC):
    @abc.abstractmethod
    def save(self, organization_id: uuid.UUID, data: bytes, *, suffix: str = "") -> str:
        """Persist bytes and return an opaque storage key."""

    @abc.abstractmethod
    def load(self, key: str) -> bytes:
        """Load bytes by storage key."""

    @abc.abstractmethod
    def delete(self, key: str) -> None:
        """Delete by storage key (idempotent)."""


class LocalDocumentStorage(DocumentStorage):
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or settings.KNOWLEDGE_STORAGE_DIR).resolve()

    def _resolve(self, key: str) -> Path:
        # Keys are server-generated ("<org>/<uuid><suffix>"). Reject anything that
        # would escape the storage root.
        target = (self._root / key).resolve()
        if not str(target).startswith(str(self._root)):
            raise ValueError("Invalid storage key (path traversal attempt).")
        return target

    def save(self, organization_id: uuid.UUID, data: bytes, *, suffix: str = "") -> str:
        safe_suffix = "".join(c for c in suffix if c.isalnum() or c in ".-_")[:16]
        key = f"{organization_id}/{uuid.uuid4().hex}{safe_suffix}"
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()


def get_document_storage() -> DocumentStorage:
    return LocalDocumentStorage()
