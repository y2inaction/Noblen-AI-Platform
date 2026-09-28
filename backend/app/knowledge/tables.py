"""Structured retrieval over tabular knowledge (Noblen AI 3.0, M3).

Vector search is the wrong tool for "how many orders from Kano in March?" or
"total outstanding by customer". Tables extracted from CSV/XLSX documents are
queried here with a small, declarative spec — filters, one aggregate, optional
group-by, ordering and a row limit. There is no free-form SQL: column names are
checked against the table's typed schema, values are bound parameters, and the
query runs in SQL (JSON path expressions) inside the same tenant and access
rules as semantic search.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.knowledge.access import Principal, document_readable, knowledge_base_readable
from app.models.enums import DocumentStatus, KnowledgeBaseStatus
from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeTable,
    KnowledgeTableRow,
)

FilterOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"]
_NUMERIC_ONLY = {"gt", "gte", "lt", "lte"}


class TableFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=64)
    op: FilterOp
    value: str | int | float | None = None


class TableAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["count", "sum", "avg", "min", "max"]
    column: str | None = Field(default=None, max_length=64)


class TableOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=64)
    descending: bool = False


class TableQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] | None = Field(default=None, max_length=50)
    filters: list[TableFilter] = Field(default_factory=list, max_length=20)
    aggregate: TableAggregate | None = None
    group_by: str | None = Field(default=None, max_length=64)
    order_by: TableOrder | None = None
    limit: int = Field(default=50, ge=1, le=500)


def _readable_tables(principal: Principal, knowledge_base_ids: list[uuid.UUID] | None) -> Any:
    stmt = (
        select(KnowledgeTable, KnowledgeDocument.name.label("document_name"))
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeTable.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeTable.knowledge_base_id)
        .where(
            KnowledgeTable.organization_id == principal.organization_id,
            KnowledgeBase.status == KnowledgeBaseStatus.ACTIVE.value,
            KnowledgeDocument.status == DocumentStatus.READY.value,
            knowledge_base_readable(principal),
            document_readable(principal),
        )
    )
    if knowledge_base_ids is not None:
        stmt = stmt.where(KnowledgeTable.knowledge_base_id.in_(knowledge_base_ids))
    return stmt


def describe(table: KnowledgeTable, document_name: str) -> dict[str, Any]:
    return {
        "table_id": str(table.id),
        "name": table.name,
        "document_id": str(table.document_id),
        "document_name": document_name,
        "knowledge_base_id": str(table.knowledge_base_id),
        "columns": table.columns,
        "row_count": table.row_count,
    }


async def list_tables(
    db: AsyncSession,
    principal: Principal,
    *,
    knowledge_base_ids: list[uuid.UUID] | None = None,
    document_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    stmt = _readable_tables(principal, knowledge_base_ids)
    if document_id is not None:
        stmt = stmt.where(KnowledgeTable.document_id == document_id)
    rows = await db.execute(stmt.order_by(KnowledgeDocument.name, KnowledgeTable.name).limit(200))
    return [describe(table, name) for table, name in rows.tuples().all()]


async def get_table(
    db: AsyncSession,
    principal: Principal,
    table_id: uuid.UUID,
    *,
    knowledge_base_ids: list[uuid.UUID] | None = None,
) -> tuple[KnowledgeTable, str]:
    row = (
        await db.execute(
            _readable_tables(principal, knowledge_base_ids).where(KnowledgeTable.id == table_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Table not found.")
    return row[0], row[1]


def _schema(table: KnowledgeTable) -> dict[str, str]:
    return {c["name"]: c["type"] for c in table.columns}


def _expr(column: str, ctype: str) -> ColumnElement[Any]:
    value = KnowledgeTableRow.data[column]
    return value.as_float() if ctype == "number" else value.as_string()


def _require(schema: dict[str, str], column: str) -> str:
    if column not in schema:
        raise ValidationError(
            f"Unknown column '{column}'. Available: {', '.join(sorted(schema)) or 'none'}."
        )
    return schema[column]


def _filter_clause(schema: dict[str, str], flt: TableFilter) -> ColumnElement[bool]:
    ctype = _require(schema, flt.column)
    expr = _expr(flt.column, ctype)
    if flt.op == "is_null":
        return expr.is_(None)
    if flt.op == "not_null":
        return expr.is_not(None)
    if flt.value is None:
        raise ValidationError(f"Filter '{flt.op}' on '{flt.column}' needs a value.")
    if flt.op == "contains":
        if ctype != "text":
            raise ValidationError(f"'contains' needs a text column; '{flt.column}' is numeric.")
        return func.lower(expr).contains(str(flt.value).lower(), autoescape=True)
    if ctype == "number":
        try:
            value: Any = float(flt.value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"'{flt.column}' is numeric; '{flt.value}' is not.") from exc
    else:
        if flt.op in _NUMERIC_ONLY:
            raise ValidationError(f"'{flt.op}' needs a numeric column; '{flt.column}' is text.")
        value = str(flt.value)
    return {
        "eq": expr == value,
        "ne": expr != value,
        "gt": expr > value,
        "gte": expr >= value,
        "lt": expr < value,
        "lte": expr <= value,
    }[flt.op]


def _clean(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, float):
        return round(value, 6)
    return value


async def query_table(
    db: AsyncSession,
    principal: Principal,
    table_id: uuid.UUID,
    query: TableQuery,
    *,
    knowledge_base_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Run a structured query. Raises NotFoundError / ValidationError."""
    table, document_name = await get_table(
        db, principal, table_id, knowledge_base_ids=knowledge_base_ids
    )
    schema = _schema(table)
    limit = min(query.limit, settings.KNOWLEDGE_MAX_QUERY_ROWS)
    where = [
        KnowledgeTableRow.table_id == table.id,
        KnowledgeTableRow.organization_id == principal.organization_id,
        *[_filter_clause(schema, f) for f in query.filters],
    ]
    source = {"table_id": str(table.id), "table": table.name, "document_name": document_name}

    if query.aggregate is not None:
        agg = query.aggregate
        if agg.op == "count":
            metric: ColumnElement[Any] = (
                func.count(_expr(agg.column, _require(schema, agg.column)))
                if agg.column
                else func.count(KnowledgeTableRow.id)
            )
        else:
            if not agg.column:
                raise ValidationError(f"'{agg.op}' needs a column.")
            if _require(schema, agg.column) != "number":
                raise ValidationError(f"'{agg.op}' needs a numeric column; '{agg.column}' is text.")
            numeric = _expr(agg.column, "number")
            if agg.op == "sum":
                metric = func.sum(numeric)
            elif agg.op == "avg":
                metric = func.avg(numeric)
            elif agg.op == "min":
                metric = func.min(numeric)
            else:
                metric = func.max(numeric)
        label = f"{agg.op}_{agg.column}" if agg.column else agg.op

        if query.group_by is None:
            value: Any = (await db.execute(select(metric).where(*where))).scalar_one()
            return {
                **source,
                "columns": [label],
                "rows": [{label: _clean(value)}],
                "truncated": False,
            }

        group_type = _require(schema, query.group_by)
        group = _expr(query.group_by, group_type)
        stmt = (
            select(group.label("group"), metric.label("metric"))
            .where(*where)
            .group_by(group)
            .order_by(metric.desc())
            .limit(limit + 1)
        )
        result = (await db.execute(stmt)).all()
        rows = [{query.group_by: _clean(g), label: _clean(m)} for g, m in result[:limit]]
        return {
            **source,
            "columns": [query.group_by, label],
            "rows": rows,
            "truncated": len(result) > limit,
        }

    if query.group_by is not None:
        raise ValidationError("'group_by' needs an 'aggregate'.")
    columns = query.columns or list(schema)
    for column in columns:
        _require(schema, column)
    stmt = select(KnowledgeTableRow.data).where(*where)
    if query.order_by is not None:
        order = _expr(query.order_by.column, _require(schema, query.order_by.column))
        stmt = stmt.order_by(order.desc() if query.order_by.descending else order.asc())
    stmt = stmt.order_by(KnowledgeTableRow.row_index).limit(limit + 1)
    data: list[dict[str, Any]] = list((await db.execute(stmt)).scalars().all())
    rows = [{c: row.get(c) for c in columns} for row in data[:limit]]
    total = (await db.execute(select(func.count(KnowledgeTableRow.id)).where(*where))).scalar_one()
    return {
        **source,
        "columns": columns,
        "rows": rows,
        "matched_rows": int(total),
        "truncated": len(data) > limit,
    }
