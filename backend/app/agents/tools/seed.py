"""Idempotent seeding of the built-in tool catalogue into the `tools` table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.builtins import BUILTIN_TOOLS
from app.models.tool import Tool


async def seed_builtin_tools(db: AsyncSession) -> int:
    """Insert any built-in tools not already present (matched by name). Returns count added."""
    existing = set((await db.execute(select(Tool.name))).scalars().all())
    added = 0
    for handler in BUILTIN_TOOLS:
        if handler.name in existing:
            continue
        db.add(
            Tool(
                name=handler.name,
                description=handler.description,
                version=handler.version,
                input_schema=handler.input_schema,
                output_schema=handler.output_schema,
                tool_type=handler.tool_type,
                permission_mode=handler.default_permission_mode,
                enabled=True,
                handler_identifier=handler.handler_identifier,
            )
        )
        added += 1
    if added:
        await db.flush()
    return added
