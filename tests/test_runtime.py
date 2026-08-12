from __future__ import annotations

import json

from minimal_agent.storage import SessionStore

from .fakes import RepeatingToolLLM, ScriptedLLM, text_response, tool_response


def test_direct_chat_returns_without_tool(runtime_factory):
    llm = ScriptedLLM([text_response("你好，我可以直接回答。")])
    runtime, _, _ = runtime_factory(llm)

    result = runtime.run(user_id="user-a", session_id="window-1", user_input="你好")

    assert result.answer == "你好，我可以直接回答。"
    assert result.iterations == 1
    assert result.exit_reason == "final_answer"
    assert {tool["name"] for tool in llm.calls[0]["tools"]} == {
        "calculator",
        "search",
        "weather",
        "todo",
        "tool_search",
    }


def test_calculator_tool_result_is_fed_back_to_model(runtime_factory):
    llm = ScriptedLLM(
        [
            tool_response(("calc-1", "calculator", {"expression": "2 + 3 * 4"})),
            text_response("结果是 14。"),
        ]
    )
    runtime, _, _ = runtime_factory(llm)

    result = runtime.run(
        user_id="user-a", session_id="window-1", user_input="计算 2+3*4"
    )

    tool_result = llm.calls[1]["messages"][-1]["content"][0]
    assert json.loads(tool_result["content"])["result"] == 14
    assert result.answer == "结果是 14。"
    assert result.iterations == 2


def test_multiple_tools_in_one_turn_and_session_todo(runtime_factory):
    llm = ScriptedLLM(
        [
            tool_response(
                ("weather-1", "weather", {"city": "上海"}),
                ("todo-1", "todo", {"action": "add", "title": "下班带伞"}),
                text="我会查天气并记录待办。",
            ),
            text_response("上海今天多云，已记录“下班带伞”。"),
        ]
    )
    runtime, store, _ = runtime_factory(llm)

    result = runtime.run(
        user_id="user-a",
        session_id="window-1",
        user_input="查上海天气并提醒我下班带伞",
    )

    assert result.iterations == 2
    assert store.list_todos("user-a", "window-1")[0]["title"] == "下班带伞"
    assert "我会查天气" in json.dumps(
        store.get_messages("user-a", "window-1")[1].content, ensure_ascii=False
    )


def test_two_windows_are_isolated_and_survive_store_restart(runtime_factory, tmp_path):
    llm = ScriptedLLM(
        [
            tool_response(("t1", "todo", {"action": "add", "title": "窗口一待办"})),
            text_response("窗口一已记录。"),
            tool_response(("t2", "todo", {"action": "add", "title": "窗口二待办"})),
            text_response("窗口二已记录。"),
        ]
    )
    runtime, store, _ = runtime_factory(llm)

    runtime.run(user_id="user-a", session_id="window-1", user_input="记待办一")
    runtime.run(user_id="user-a", session_id="window-2", user_input="记待办二")

    assert [item["title"] for item in store.list_todos("user-a", "window-1")] == [
        "窗口一待办"
    ]
    assert [item["title"] for item in store.list_todos("user-a", "window-2")] == [
        "窗口二待办"
    ]
    reopened = SessionStore(tmp_path / "agent.db")
    assert reopened.list_todos("user-a", "window-1")[0]["title"] == "窗口一待办"


def test_tool_follow_up_receives_same_session_history(runtime_factory):
    llm = ScriptedLLM(
        [
            tool_response(("w1", "weather", {"city": "上海", "day": "today"})),
            text_response("上海今天 30°C，多云。"),
            tool_response(("w2", "weather", {"city": "上海", "day": "tomorrow"})),
            text_response("上海明天 31°C，有阵雨。"),
        ]
    )
    runtime, _, _ = runtime_factory(llm)

    runtime.run(user_id="user-a", session_id="weather", user_input="上海今天天气？")
    result = runtime.run(user_id="user-a", session_id="weather", user_input="那明天呢？")

    follow_up_context = json.dumps(llm.calls[2]["messages"], ensure_ascii=False)
    assert "temperature_c" in follow_up_context
    assert "那明天呢" in follow_up_context
    assert result.answer == "上海明天 31°C，有阵雨。"


def test_plain_chat_follow_up_receives_previous_turn(runtime_factory):
    llm = ScriptedLLM(
        [text_response("我叫小明，喜欢蓝色。"), text_response("你刚才说喜欢蓝色。")]
    )
    runtime, _, _ = runtime_factory(llm)

    runtime.run(user_id="user-a", session_id="chat", user_input="我叫小明，喜欢蓝色")
    result = runtime.run(user_id="user-a", session_id="chat", user_input="我喜欢什么颜色？")

    second_context = json.dumps(llm.calls[1]["messages"], ensure_ascii=False)
    assert "喜欢蓝色" in second_context
    assert "我喜欢什么颜色" in second_context
    assert result.answer == "你刚才说喜欢蓝色。"


def test_unknown_tool_error_is_returned_to_llm(runtime_factory):
    llm = ScriptedLLM(
        [tool_response(("bad-1", "not_registered", {})), text_response("工具不存在。")]
    )
    runtime, _, _ = runtime_factory(llm)

    runtime.run(user_id="user-a", session_id="window-1", user_input="调用未知工具")

    result_block = llm.calls[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True
    assert "unknown tool" in result_block["content"]


def test_max_iterations_stops_infinite_tool_loop(runtime_factory):
    llm = RepeatingToolLLM()
    runtime, _, trace_path = runtime_factory(llm, max_iterations=2)

    result = runtime.run(user_id="user-a", session_id="loop", user_input="一直计算")

    assert result.exit_reason == "max_iterations"
    assert result.iterations == 2
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["exit_reason"] == "max_iterations"
    assert sum(event["event"] == "tool_finished" for event in events) == 2


def test_long_context_is_compacted_and_recalled_in_system(runtime_factory):
    long_answer = "这是很长的回答。" * 30
    llm = ScriptedLLM(
        [
            text_response(long_answer),
            text_response(long_answer),
            text_response("我记得前面的摘要。"),
        ],
        summary="用户正在测试长会话，早期回答已压缩。",
    )
    runtime, store, _ = runtime_factory(
        llm, context_max_characters=250, keep_recent_messages=2
    )

    runtime.run(user_id="user-a", session_id="compact", user_input="第一轮长对话")
    runtime.run(user_id="user-a", session_id="compact", user_input="第二轮长对话")
    runtime.run(user_id="user-a", session_id="compact", user_input="还记得吗？")

    summary, through_id = store.get_memory("user-a", "compact")
    assert llm.summary_calls
    assert summary == "用户正在测试长会话，早期回答已压缩。"
    assert through_id > 0
    assert "session_summary" in llm.calls[-1]["system"]
    assert summary in llm.calls[-1]["system"]


def test_compaction_preserves_four_complete_turns_including_tool_chains(runtime_factory):
    responses = []
    for index in range(1, 6):
        responses.extend(
            [
                tool_response(
                    (f"calc-{index}", "calculator", {"expression": f"{index} + 1"})
                ),
                text_response(f"第 {index} 轮结果是 {index + 1}。"),
            ]
        )
    llm = ScriptedLLM(responses, summary="第 1 轮已压缩。")
    runtime, store, _ = runtime_factory(
        llm,
        context_max_characters=1_000_000,
        context_max_tokens=128,
        context_window_tokens=8_192,
        reserved_output_tokens=512,
        keep_recent_turns=4,
    )

    for index in range(1, 6):
        runtime.run(
            user_id="user-a",
            session_id="four-turns",
            user_input=f"第 {index} 轮，请计算 {index} + 1",
        )

    summary, through_id = store.get_memory("user-a", "four-turns")
    remaining = store.get_messages("user-a", "four-turns", after_id=through_id)
    external_user_messages = [
        message
        for message in remaining
        if message.role == "user" and isinstance(message.content, str)
    ]

    assert summary == "第 1 轮已压缩。"
    assert [message.content for message in external_user_messages] == [
        "第 2 轮，请计算 2 + 1",
        "第 3 轮，请计算 3 + 1",
        "第 4 轮，请计算 4 + 1",
        "第 5 轮，请计算 5 + 1",
    ]
    assert sum(
        1
        for message in remaining
        if isinstance(message.content, list)
        and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in message.content
        )
    ) == 4


def test_trace_contains_complete_request_chain(runtime_factory):
    llm = ScriptedLLM(
        [
            tool_response(("s1", "search", {"query": "session"})),
            text_response("找到 Session 隔离指南。"),
        ]
    )
    runtime, _, trace_path = runtime_factory(llm)

    result = runtime.run(user_id="user-a", session_id="trace", user_input="搜索 session")

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert {event["trace_id"] for event in events} == {result.trace_id}
    event_names = [event["event"] for event in events]
    assert event_names[0] == "request_started"
    assert event_names[-1] == "request_finished"
    assert event_names.count("tool_schema_selected") == 2
    assert event_names.count("context_preflight") == 2
    assert event_names.count("llm_started") == 2
    assert event_names.count("llm_finished") == 2
    assert event_names.count("tool_started") == 1
    assert event_names.count("tool_finished") == 1
    preflight = next(event for event in events if event["event"] == "context_preflight")
    assert preflight["tool_schema_tokens"] > 0


def test_model_timeout_returns_safe_failure_instead_of_raising(runtime_factory):
    class TimeoutLLM:
        def complete(self, *, system, messages, tools):
            raise TimeoutError("provider timed out with internal details")

        def summarize(self, *, previous_summary, transcript):
            return "summary"

    runtime, store, trace_path = runtime_factory(TimeoutLLM())

    result = runtime.run(user_id="user-a", session_id="failure", user_input="你好")

    assert result.exit_reason == "model_timeout"
    assert "超时" in result.answer
    assert "internal details" not in result.answer
    assert store.get_messages("user-a", "failure")[-1].role == "assistant"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "request_failed"
    assert events[-1]["error_code"] == "model_timeout"


def test_context_too_long_runs_one_reactive_compaction_retry(runtime_factory):
    class RetryLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, *, system, messages, tools):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("context_length_exceeded: prompt is too long")
            return text_response("压缩重试成功。")

        def summarize(self, *, previous_summary, transcript):
            return "summary"

    llm = RetryLLM()
    runtime, _, trace_path = runtime_factory(llm)

    result = runtime.run(user_id="user-a", session_id="retry", user_input="继续")

    assert result.answer == "压缩重试成功。"
    assert llm.calls == 2
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "context_reactive_compaction" for event in events) == 1
    assert sum(event["event"] == "llm_started" for event in events) == 2
