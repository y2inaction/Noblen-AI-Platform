"""Background agent worker: executes QUEUED runs outside the HTTP request.

The database is the queue. A run admitted with `background=True` is stored as
QUEUED; workers claim the oldest one with `SELECT ... FOR UPDATE SKIP LOCKED`
(PostgreSQL), so several workers can run side by side without taking the same
run, and no extra broker is required. Run state is persisted per step, so the
trace, approvals and escalations behave exactly as for synchronous runs.

Between runs the worker also does housekeeping: deleting long-term memories
past their organization's retention period (M4).

Run it with:  python -m app.agents.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import AgentRuntime
from app.ai.errors import AIError
from app.core.config import settings
from app.core.logging import clear_context, configure_logging, get_logger
from app.models.enums import RunStatus
from app.models.run import AgentRun
from app.services import memory_service

logger = get_logger("agents.worker")


async def claim_next_run(db: AsyncSession) -> uuid.UUID | None:
    """Atomically move the oldest QUEUED run to RUNNING and return its id."""
    run = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.status == RunStatus.QUEUED.value)
            .order_by(AgentRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if run is None:
        await db.rollback()
        return None
    run.status = RunStatus.RUNNING.value
    await db.commit()
    return run.id


async def process_next(
    session_factory: Callable[[], AsyncSession], runtime: AgentRuntime
) -> uuid.UUID | None:
    """Claim and execute one queued run. Returns its id, or None if idle."""
    async with session_factory() as db:
        run_id = await claim_next_run(db)
    if run_id is None:
        return None
    clear_context()
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:  # pragma: no cover - deleted between claim and load
            return run_id
        try:
            result = await runtime.process_queued(db, run)
            await db.commit()
            logger.info("worker_run_finished", run_id=str(run_id), status=result.status)
        except AIError:
            # The runtime already persisted the run as FAILED.
            logger.warning("worker_run_failed", run_id=str(run_id))
        except Exception:
            logger.exception("worker_run_crashed", run_id=str(run_id))
            await db.rollback()
            crashed = await db.get(AgentRun, run_id)
            if crashed is not None and crashed.status in (
                RunStatus.RUNNING.value,
                RunStatus.QUEUED.value,
            ):
                crashed.status = RunStatus.FAILED.value
                crashed.error_code = "worker_error"
                await db.commit()
    return run_id


async def purge_expired_memories(session_factory: Callable[[], AsyncSession]) -> int:
    """Delete memories past retention. Failures are logged, never fatal."""
    try:
        async with session_factory() as db:
            removed = await memory_service.purge_expired(db)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_purge_failed", error_type=type(exc).__name__)
        return 0
    if removed:
        logger.info("memory_purged", removed=removed)
    return removed


async def run_worker(
    session_factory: Callable[[], AsyncSession],
    runtime: AgentRuntime,
    *,
    poll_seconds: float,
    stop: asyncio.Event,
) -> None:
    logger.info("worker_started", poll_seconds=poll_seconds)
    last_purge = float("-inf")
    while not stop.is_set():
        if time.monotonic() - last_purge >= settings.MEMORY_PURGE_INTERVAL_SECONDS:
            await purge_expired_memories(session_factory)
            last_purge = time.monotonic()
        processed = await process_next(session_factory, runtime)
        if processed is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
    logger.info("worker_stopped")


def main() -> None:  # pragma: no cover - process entrypoint
    from app.db.session import SessionLocal

    configure_logging(debug=settings.DEBUG)
    stop = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run_worker(
            SessionLocal, AgentRuntime(), poll_seconds=settings.AGENT_WORKER_POLL_SECONDS, stop=stop
        )

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    main()
