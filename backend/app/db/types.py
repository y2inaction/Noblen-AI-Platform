"""Custom SQLAlchemy column types.

`EmbeddingVector` renders as a real pgvector `vector(dim)` column on PostgreSQL
(production) and falls back to portable `JSON` on other dialects (e.g. SQLite),
so the shared metadata's `create_all` still works for the non-knowledge test
suites. Knowledge/RAG tests always run on PostgreSQL, where the pgvector operators
are available (see DECISIONS ADR-0017).
"""

from __future__ import annotations

from sqlalchemy import types
from sqlalchemy.engine import Dialect


class EmbeddingVector(types.TypeDecorator):
    """A fixed-dimension embedding vector.

    On PostgreSQL this is `pgvector.sqlalchemy.Vector(dim)`; elsewhere it is JSON.
    """

    impl = types.JSON
    cache_ok = True

    def __init__(self, dim: int, **kwargs: object) -> None:
        self.dim = dim
        super().__init__(**kwargs)

    def load_dialect_impl(self, dialect: Dialect):  # noqa: ANN201
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(types.JSON())
