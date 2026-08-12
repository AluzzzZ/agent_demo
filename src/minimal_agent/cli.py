from __future__ import annotations

import argparse
import os
from pathlib import Path

from .env import load_project_env
from .llm import AnthropicLLM, DashScopeLLM
from .runtime import AgentRuntime
from .storage import SessionStore
from .tools import create_default_registry
from .tracing import TraceRecorder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Framework-free minimal Agent")
    parser.add_argument("--user", required=True, help="logical user id")
    parser.add_argument("--session", required=True, help="independent window/session id")
    parser.add_argument("--once", help="run one prompt and exit instead of interactive mode")
    parser.add_argument(
        "--provider",
        choices=["dashscope", "anthropic"],
        default=os.getenv("LLM_PROVIDER", "dashscope"),
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--base-url",
        default=os.getenv("DASHSCOPE_BASE_URL") or os.getenv("BASE_URL"),
        help="DashScope native /api/v1 or OpenAI-compatible base URL",
    )
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=24)
    parser.add_argument("--max-total-tokens", type=int, default=None)
    parser.add_argument("--context-max-tokens", type=int, default=6_000)
    parser.add_argument("--context-window-tokens", type=int, default=32_768)
    parser.add_argument("--keep-recent-turns", type=int, default=4)
    parser.add_argument(
        "--db", default=os.getenv("AGENT_DB_PATH", str(Path("data") / "agent.db"))
    )
    parser.add_argument(
        "--trace",
        default=os.getenv("AGENT_TRACE_PATH", str(Path("data") / "traces.jsonl")),
    )
    return parser


def build_llm(args):
    if args.provider == "dashscope":
        return DashScopeLLM(model=args.model, base_url=args.base_url)
    return AnthropicLLM(model=args.model)


def main() -> None:
    load_project_env()
    args = build_parser().parse_args()
    runtime = AgentRuntime(
        llm=build_llm(args),
        store=SessionStore(args.db),
        tools=create_default_registry(),
        tracer=TraceRecorder(args.trace),
        max_iterations=args.max_iterations,
        max_tool_calls=args.max_tool_calls,
        max_total_tokens=args.max_total_tokens,
        context_max_tokens=args.context_max_tokens,
        context_window_tokens=args.context_window_tokens,
        keep_recent_turns=args.keep_recent_turns,
    )

    if args.once:
        result = runtime.run(
            user_id=args.user, session_id=args.session, user_input=args.once
        )
        print(result.answer)
        print(f"[trace_id={result.trace_id}, iterations={result.iterations}]")
        return

    print(
        f"Minimal Agent | user={args.user} session={args.session} | 输入 /exit 退出"
    )
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if prompt in {"/exit", "/quit"}:
            break
        if not prompt:
            continue
        try:
            result = runtime.run(
                user_id=args.user, session_id=args.session, user_input=prompt
            )
            print(f"agent> {result.answer}")
            print(f"       trace={result.trace_id} iterations={result.iterations}")
        except Exception as exc:
            print(f"error> {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
