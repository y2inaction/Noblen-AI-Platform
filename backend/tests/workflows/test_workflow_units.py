"""Workflow definitions, templating and scheduling (no database)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.workflows.definition import parse
from app.workflows.engine import _compare
from app.workflows.service import next_fire
from app.workflows.templating import TemplateError, check_references, render

_CTX = {
    "input": {"name": "Ada", "count": 3, "tags": ["a", "b"]},
    "steps": {"s1": {"output": {"total": 7, "rows": [{"x": 1}]}}},
}


def test_whole_reference_keeps_type_and_text_interpolates():
    assert render("{{ input.count }}", _CTX) == 3
    assert render("{{steps.s1.output.rows.0.x}}", _CTX) == 1
    assert render("Hi {{ input.name }} ({{ input.count }})", _CTX) == "Hi Ada (3)"
    assert render("tags: {{ input.tags }}", _CTX) == 'tags: ["a", "b"]'
    assert render({"a": ["{{ input.name }}", 5]}, _CTX) == {"a": ["Ada", 5]}


def test_missing_and_foreign_references_fail():
    with pytest.raises(TemplateError):
        render("{{ input.missing }}", _CTX)
    with pytest.raises(TemplateError):
        check_references({"x": "{{ __import__.os }}"})
    # Anything that is not a plain dotted path is left as literal text.
    assert render("{{ input.name | upper }}", _CTX) == "{{ input.name | upper }}"


def _steps(*steps):
    return {"steps": list(steps)}


@pytest.mark.parametrize(
    "definition",
    [
        _steps({"id": "a", "type": "tool", "tool": "list_tasks", "next": "nowhere"}),
        _steps(
            {"id": "a", "type": "tool", "tool": "list_tasks"},
            {"id": "a", "type": "tool", "tool": "list_tasks"},
        ),
        _steps({"id": "end", "type": "tool", "tool": "list_tasks"}),
        _steps({"id": "Bad-Id", "type": "tool", "tool": "list_tasks"}),
        _steps({"id": "a", "type": "shell", "command": "rm -rf /"}),
        {"trigger": {"type": "schedule"}, **_steps({"id": "a", "type": "tool", "tool": "x"})},
        {
            "trigger": {"type": "schedule", "every_minutes": 1},
            **_steps({"id": "a", "type": "tool", "tool": "x"}),
        },
        {
            "trigger": {"type": "schedule", "daily_at": "25:00"},
            **_steps({"id": "a", "type": "tool", "tool": "x"}),
        },
        {"trigger": {"type": "event"}, **_steps({"id": "a", "type": "tool", "tool": "x"})},
        {
            "trigger": {"type": "manual", "event": "task.created"},
            **_steps({"id": "a", "type": "tool", "tool": "x"}),
        },
        _steps({"id": "a", "type": "tool", "tool": "x", "arguments": {"t": "{{ secrets.key }}"}}),
        {"steps": []},
    ],
)
def test_invalid_definitions_are_rejected(definition):
    with pytest.raises((ValidationError, TemplateError)):
        parse(definition)


def test_default_flow_and_branch_targets():
    definition = parse(
        _steps(
            {"id": "a", "type": "tool", "tool": "list_tasks"},
            {
                "id": "b",
                "type": "condition",
                "left": 1,
                "op": "eq",
                "right": 1,
                "then": "c",
                "else": "end",
            },
            {"id": "c", "type": "approval", "title": "ok?", "on_reject": "a"},
        )
    )
    assert definition.next_of(definition.step("a")) == "b"
    assert definition.next_of(definition.step("c")) is None
    assert definition.step("b").else_ == "end"


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        ("eq", "3", 3, True),
        ("ne", "a", "b", True),
        ("gt", "10", 9, True),
        ("lte", 2.5, "2.5", True),
        ("contains", "Urgent refund", "REFUND", True),
        ("contains", ["x", "y"], "y", True),
        ("exists", None, None, False),
        ("empty", [], None, True),
    ],
)
def test_condition_operators(op, left, right, expected):
    assert _compare(op, left, right) is expected


def test_numeric_comparison_of_text_is_an_error():
    with pytest.raises(TemplateError):
        _compare("gt", "abc", 1)


def test_daily_schedule_uses_the_organization_timezone():
    lagos = ZoneInfo("Africa/Lagos")  # UTC+1, no DST
    definition = parse(
        {
            "trigger": {"type": "schedule", "daily_at": "08:00"},
            **_steps({"id": "a", "type": "tool", "tool": "x"}),
        }
    )
    before = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)  # 07:00 in Lagos
    assert next_fire(definition, before, lagos) == datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    after = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)  # exactly 08:00 → tomorrow
    assert next_fire(definition, after, lagos) == datetime(2026, 9, 28, 7, 0, tzinfo=UTC)
    every = parse(
        {
            "trigger": {"type": "schedule", "every_minutes": 30},
            **_steps({"id": "a", "type": "tool", "tool": "x"}),
        }
    )
    assert next_fire(every, before, lagos) == datetime(2026, 9, 27, 6, 30, tzinfo=UTC)
    manual = parse(_steps({"id": "a", "type": "tool", "tool": "x"}))
    assert next_fire(manual, before, lagos) is None
