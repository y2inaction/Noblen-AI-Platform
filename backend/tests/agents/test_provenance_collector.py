"""Unit tests for the provenance collector (M8.4) beyond the M8.2 contract test."""

from __future__ import annotations

import uuid

import pytest

import app.agents.provenance as provenance
from app.agents.provenance import ProvenanceCollector, normalize


def _id() -> str:
    return str(uuid.uuid4())


def test_references_are_canonical():
    raw = uuid.uuid4()
    assert normalize({"type": "integration", "id": raw.hex.upper()}) == {
        "type": "integration",
        "id": str(raw),
    }
    assert normalize({"type": "external_input"}) == {"type": "external_input"}
    mem = _id()
    assert normalize({"type": "memory", "id": mem, "scope": "USER"}) == {
        "type": "memory",
        "id": mem,
        "scope": "USER",
    }


@pytest.mark.parametrize(
    "ref",
    [
        {"type": "external_input", "id": str(uuid.uuid4())},  # no id for input
        {"type": "external_input", "payload": {"x": 1}},  # never the payload
        {"type": "memory", "id": str(uuid.uuid4())},  # scope required
        {"type": "memory", "id": str(uuid.uuid4()), "scope": "EVERYONE"},
        {"type": "knowledge_document", "id": str(uuid.uuid4()), "scope": "USER"},
        {"type": "knowledge_document", "id": "not-a-uuid"},
        {"type": "knowledge_document"},
        "knowledge_document",
    ],
)
def test_malformed_references_are_rejected(ref):
    with pytest.raises(ValueError):
        ProvenanceCollector().add(ref)


def test_external_input_and_duplicates_collapse_deterministically():
    c = ProvenanceCollector()
    doc = _id()
    c.add({"type": "external_input"})
    c.add({"type": "knowledge_document", "id": doc})
    c.add({"type": "external_input"})
    c.add({"type": "knowledge_document", "id": doc.upper()})
    assert c.snapshot() == (
        [{"type": "external_input"}, {"type": "knowledge_document", "id": doc}],
        False,
    )


def test_the_limit_is_read_at_add_time(monkeypatch):
    monkeypatch.setattr(provenance, "MAX_SOURCES", 1)
    c = ProvenanceCollector()
    c.add({"type": "integration", "id": _id()})
    c.add({"type": "integration", "id": _id()})
    assert len(c.sources) == 1 and c.truncated is True


def test_inherit_merges_and_fails_closed_on_unknown_upstream():
    upstream = [{"type": "memory", "id": _id(), "scope": "ORGANIZATION"}]
    c = ProvenanceCollector()
    c.inherit(upstream, truncated=False)
    assert c.sources == upstream and c.truncated is False

    unknown = ProvenanceCollector()
    unknown.inherit(None, truncated=False)  # pre-M8 upstream: unknown
    assert unknown.truncated is True

    cut = ProvenanceCollector()
    cut.inherit(upstream, truncated=True)  # truncation propagates
    assert cut.sources == upstream and cut.truncated is True

    corrupt = ProvenanceCollector()
    corrupt.inherit([{"type": "memory", "id": _id()}], truncated=False)  # no scope
    assert corrupt.sources == [] and corrupt.truncated is True


def test_sources_are_copies():
    c = ProvenanceCollector()
    c.add({"type": "agent_run", "id": _id()})
    c.sources[0]["id"] = "tampered"
    assert c.sources[0]["id"] != "tampered"
