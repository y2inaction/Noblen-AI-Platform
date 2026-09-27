"""History sanitation: incomplete tool-call pairs never reach a provider."""

from app.agents.memory import sanitize_tool_pairs
from app.ai.types import Message, ToolCall


def test_drops_orphan_results_and_unanswered_calls():
    history = [
        Message(role="tool", content="{}", tool_call_id="cut_off"),  # call windowed out
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a", name="x")]),
        Message(role="tool", content="{}", tool_call_id="a"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="pay"),
        # Run ended while awaiting approval: call never answered.
        Message(role="assistant", content="Paying.", tool_calls=[ToolCall(id="b", name="pay")]),
    ]
    cleaned = sanitize_tool_pairs(history)
    assert [m.role for m in cleaned] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
        "assistant",
    ]
    assert cleaned[-1].tool_calls is None and cleaned[-1].content == "Paying."
