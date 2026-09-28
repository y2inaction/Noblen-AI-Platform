"""Run provenance: which protected sources a run's content derives from (ADR-0037).

A `ProvenanceCollector` lives for one run. Only trusted server-side capture
points add to it: knowledge retrieval, memory injection and recall, the
integration gateway, workflow templating and run input. It is never populated
from API input or model output. What it holds is persisted to the run's
`sources` / `sources_truncated` columns, and the read rule
(docs/architecture/milestone-8-provenance.md §6) decides visibility from it at
read time.

A reference names a row the platform already authorizes: a type and an id, plus
the scope for memories, or just the type for `external_input`. It never carries
text (invariant I2). At most `MAX_SOURCES` distinct references are kept. Beyond
that the collector is marked truncated, and truncated provenance fails closed
(I3).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from app.models.enums import MemoryScope

MAX_SOURCES = 500

KNOWLEDGE_DOCUMENT = "knowledge_document"
KNOWLEDGE_TABLE = "knowledge_table"
MEMORY = "memory"
INTEGRATION = "integration"
WORKFLOW_STEP = "workflow_step"
AGENT_RUN = "agent_run"
EXTERNAL_INPUT = "external_input"

SOURCE_TYPES = frozenset(
    {
        KNOWLEDGE_DOCUMENT,
        KNOWLEDGE_TABLE,
        MEMORY,
        INTEGRATION,
        WORKFLOW_STEP,
        AGENT_RUN,
        EXTERNAL_INPUT,
    }
)
_MEMORY_SCOPES = frozenset(scope.value for scope in MemoryScope)

Reference = dict[str, str]


def normalize(ref: Mapping[str, Any]) -> Reference:
    """Validate one reference and return it in canonical form.

    Raises ValueError for anything that is not exactly a known type with a
    well-formed id (and, for memories, a known scope). Extra keys are rejected,
    so text can never ride along.
    """
    if not isinstance(ref, Mapping):
        raise ValueError("A provenance reference must be a mapping.")
    kind = ref.get("type")
    if kind not in SOURCE_TYPES:
        raise ValueError(f"Unknown provenance reference type: {kind!r}.")
    if kind == EXTERNAL_INPUT:
        if set(ref) != {"type"}:
            raise ValueError("external_input references carry no other fields.")
        return {"type": EXTERNAL_INPUT}
    allowed = {"type", "id", "scope"} if kind == MEMORY else {"type", "id"}
    if not set(ref) <= allowed or "id" not in ref:
        raise ValueError(f"A {kind} reference has exactly the fields {sorted(allowed)}.")
    try:
        ident = str(uuid.UUID(str(ref["id"])))
    except ValueError as exc:
        raise ValueError(f"A {kind} reference needs a UUID id.") from exc
    if kind == MEMORY:
        scope = ref.get("scope")
        if scope not in _MEMORY_SCOPES:
            raise ValueError("A memory reference needs its scope.")
        return {"type": MEMORY, "id": ident, "scope": str(scope)}
    return {"type": str(kind), "id": ident}


def _key(ref: Reference) -> tuple[str, str]:
    return ref["type"], ref.get("id", "")


class ProvenanceCollector:
    """Bounded, deduplicated set of references for one run, in insertion order."""

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str], Reference] = {}
        self._truncated = False

    @property
    def sources(self) -> list[Reference]:
        return [dict(ref) for ref in self._sources.values()]

    @property
    def truncated(self) -> bool:
        return self._truncated

    def add(self, ref: Mapping[str, Any]) -> None:
        """Record one reference. Duplicates are ignored. Past the limit, nothing is
        kept and the collector is marked truncated. Malformed references raise
        ValueError: capture points are trusted code, so this is a bug to surface."""
        clean = normalize(ref)
        key = _key(clean)
        if key in self._sources:
            return
        if len(self._sources) >= MAX_SOURCES:
            self._truncated = True
            return
        self._sources[key] = clean

    def add_all(self, refs: Iterable[Mapping[str, Any]]) -> None:
        for ref in refs:
            self.add(ref)

    def inherit(self, sources: Iterable[Mapping[str, Any]] | None, truncated: bool) -> None:
        """Take on the provenance of something this run's content derives from
        (an upstream workflow step, a child agent run). Unknown (None) or
        truncated upstream provenance makes this collector truncated too, so it
        fails closed."""
        if sources is None or truncated:
            self._truncated = True
        for ref in sources or ():
            try:
                self.add(ref)
            except ValueError:
                # Stored upstream provenance that no longer validates is unknown.
                self._truncated = True

    def snapshot(self) -> tuple[list[Reference], bool]:
        """What to persist: (sources, sources_truncated)."""
        return self.sources, self._truncated
