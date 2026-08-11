from __future__ import annotations

from minimal_agent.tracing import TraceRecorder


def test_trace_redacts_sensitive_fields_and_assigns_sequence(tmp_path):
    tracer = TraceRecorder(tmp_path / "trace.jsonl")
    common = {
        "trace_id": "trace-1",
        "user_id": "user-a",
        "session_id": "window-1",
    }
    tracer.record(
        **common,
        event="tool_started",
        arguments={"password": "plain-text", "query": "use sk-abcdefghijklmnop"},
    )
    tracer.record(**common, event="tool_finished", status="success")

    events = tracer.read_events(user_id="user-a", trace_id="trace-1")

    assert [event["sequence_no"] for event in events] == [1, 2]
    assert events[0]["arguments"]["password"] == "[REDACTED]"
    assert events[0]["arguments"]["query"] == "use [REDACTED]"
