import asyncio
import os
import uuid
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .env import load_project_env
from .llm import AnthropicLLM, DashScopeLLM
from .runtime import AgentRuntime
from .storage import SessionStore
from .tools import create_default_registry
from .tracing import TraceRecorder


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=80)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


RuntimeFactory = Callable[[], AgentRuntime]


def create_app(
    *,
    store: SessionStore | None = None,
    tracer: TraceRecorder | None = None,
    runtime_factory: RuntimeFactory | None = None,
) -> FastAPI:
    load_project_env()
    db_path = os.getenv("AGENT_DB_PATH", str(Path("data") / "agent.db"))
    trace_path = os.getenv(
        "AGENT_TRACE_PATH", str(Path("data") / "traces.jsonl")
    )
    app_store = store or SessionStore(db_path)
    app_tracer = tracer or TraceRecorder(trace_path)
    _seed_demo_users(app_store)

    if runtime_factory is None:
        runtime_factory = lambda: _build_runtime(app_store, app_tracer)

    app = FastAPI(title="Minimal Agent Workbench", version="0.2.0")
    app.state.store = app_store
    app.state.tracer = app_tracer
    app.state.runtime_factory = runtime_factory
    app.state.session_locks = {}

    static_dir = Path(__file__).with_name("web_static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/app-info")
    def app_info() -> dict[str, Any]:
        return {
            "name": "Minimal Agent",
            "search_provider": "Wikipedia / MediaWiki（免费，无 Key）",
            "weather_provider": "Open-Meteo（免费非商用，无 Key）",
            "max_iterations": int(os.getenv("AGENT_MAX_ITERATIONS", "8")),
            "max_tool_calls": int(os.getenv("AGENT_MAX_TOOL_CALLS", "24")),
            "context_max_tokens": int(os.getenv("AGENT_CONTEXT_MAX_TOKENS", "6000")),
            "keep_recent_turns": int(os.getenv("AGENT_KEEP_RECENT_TURNS", "4")),
        }

    @app.post("/api/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        user = app_store.authenticate_user(request.username, request.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误。",
            )
        token = app_store.create_auth_token(user["user_id"], ttl_seconds=7 * 86_400)
        return {"token": token, "user": user}

    def current_user(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        token = _bearer_token(authorization)
        user = app_store.resolve_auth_token(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录已失效，请重新登录。",
            )
        return {**user, "_token": token}

    @app.get("/api/auth/me")
    def me(user: Annotated[dict[str, str], Depends(current_user)]) -> dict[str, str]:
        return {key: value for key, value in user.items() if not key.startswith("_")}

    @app.post("/api/auth/logout")
    def logout(user: Annotated[dict[str, str], Depends(current_user)]) -> dict[str, bool]:
        app_store.revoke_auth_token(user["_token"])
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions(
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        return app_store.list_sessions(user["user_id"])

    @app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(
        request: SessionCreateRequest,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        app_store.ensure_session(user["user_id"], session_id, title=request.title)
        return {"session_id": session_id, "title": request.title or "新对话"}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(
        session_id: str,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> dict[str, Any]:
        user_id = user["user_id"]
        _require_session(app_store, user_id, session_id)
        lock_key = (user_id, session_id)
        lock = app.state.session_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            _require_session(app_store, user_id, session_id)
            deleted_trace_events = await run_in_threadpool(
                app_tracer.delete_session_events,
                user_id=user_id,
                session_id=session_id,
            )
            deleted = await run_in_threadpool(
                app_store.delete_session, user_id, session_id
            )
            if not deleted:
                raise HTTPException(status_code=404, detail="会话不存在。")
        app.state.session_locks.pop(lock_key, None)
        return {"ok": True, "deleted_trace_events": deleted_trace_events}

    @app.get("/api/sessions/{session_id}/messages")
    def get_messages(
        session_id: str,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        _require_session(app_store, user["user_id"], session_id)
        return [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in app_store.get_messages(user["user_id"], session_id)
        ]

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(
        session_id: str,
        request: ChatRequest,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> dict[str, Any]:
        user_id = user["user_id"]
        _require_session(app_store, user_id, session_id)
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="消息不能为空。")
        if not app_store.get_messages(user_id, session_id):
            app_store.set_session_title(user_id, session_id, _title_from_message(message))

        lock_key = (user_id, session_id)
        lock = app.state.session_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                runtime = app.state.runtime_factory()
                result = await run_in_threadpool(
                    runtime.run,
                    user_id=user_id,
                    session_id=session_id,
                    user_input=message,
                )
            except Exception as exc:
                failure_trace_id = uuid.uuid4().hex
                app_tracer.record(
                    trace_id=failure_trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="request_started",
                    phase="web_request",
                )
                app_tracer.record(
                    trace_id=failure_trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="request_failed",
                    phase="runtime_or_model",
                    error_type=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Agent 暂时无法完成请求，请在 Trace 中查看失败阶段。"
                        f" Trace ID: {failure_trace_id}"
                    ),
                ) from exc
        return {
            "answer": result.answer,
            "trace_id": result.trace_id,
            "iterations": result.iterations,
            "exit_reason": result.exit_reason,
        }

    @app.get("/api/sessions/{session_id}/traces")
    def list_traces(
        session_id: str,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        _require_session(app_store, user["user_id"], session_id)
        return app_tracer.list_traces(user_id=user["user_id"], session_id=session_id)

    @app.get("/api/traces/{trace_id}")
    def trace_detail(
        trace_id: str,
        user: Annotated[dict[str, str], Depends(current_user)],
    ) -> dict[str, Any]:
        events = app_tracer.read_events(user_id=user["user_id"], trace_id=trace_id)
        if not events:
            raise HTTPException(status_code=404, detail="Trace 不存在。")
        return {"trace_id": trace_id, "events": events}

    return app


def _build_runtime(store: SessionStore, tracer: TraceRecorder) -> AgentRuntime:
    provider = os.getenv("LLM_PROVIDER", "dashscope").lower()
    if provider == "anthropic":
        llm = AnthropicLLM(model=os.getenv("ANTHROPIC_MODEL"))
    else:
        llm = DashScopeLLM(
            model=os.getenv("DASHSCOPE_MODEL"),
            base_url=os.getenv("DASHSCOPE_BASE_URL") or os.getenv("BASE_URL"),
        )
    return AgentRuntime(
        llm=llm,
        store=store,
        tools=create_default_registry(),
        tracer=tracer,
        max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "8")),
        max_tool_calls=int(os.getenv("AGENT_MAX_TOOL_CALLS", "24")),
        max_total_tokens=_optional_env_int("AGENT_MAX_TOTAL_TOKENS"),
        context_max_tokens=int(os.getenv("AGENT_CONTEXT_MAX_TOKENS", "6000")),
        context_window_tokens=int(os.getenv("AGENT_CONTEXT_WINDOW_TOKENS", "32768")),
        reserved_output_tokens=int(os.getenv("AGENT_RESERVED_OUTPUT_TOKENS", "2048")),
        keep_recent_turns=int(os.getenv("AGENT_KEEP_RECENT_TURNS", "4")),
        full_tool_catalog_threshold=int(
            os.getenv("AGENT_FULL_TOOL_CATALOG_THRESHOLD", "12")
        ),
        max_selected_tools=int(os.getenv("AGENT_MAX_SELECTED_TOOLS", "8")),
    )


def _optional_env_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value and value.strip() else None


def _seed_demo_users(store: SessionStore) -> None:
    accounts = (
        (
            "demo-user-1",
            os.getenv("DEMO_USER_1_USERNAME", "zsw1"),
            os.getenv("DEMO_USER_1_PASSWORD", "123456"),
            os.getenv("DEMO_USER_1_DISPLAY_NAME", "zsw1"),
        ),
        (
            "demo-user-2",
            os.getenv("DEMO_USER_2_USERNAME", "zsw2"),
            os.getenv("DEMO_USER_2_PASSWORD", "123456"),
            os.getenv("DEMO_USER_2_DISPLAY_NAME", "zsw2"),
        ),
    )
    for user_id, username, password, display_name in accounts:
        store.seed_user(
            user_id=user_id,
            username=username,
            password=password,
            display_name=display_name,
        )


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录。")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录。")
    return token


def _require_session(store: SessionStore, user_id: str, session_id: str) -> None:
    if not store.session_exists(user_id, session_id):
        raise HTTPException(status_code=404, detail="会话不存在。")


def _title_from_message(message: str) -> str:
    title = " ".join(message.split())
    return title[:30] + ("…" if len(title) > 30 else "")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "minimal_agent.web:create_app",
        factory=True,
        host=os.getenv("AGENT_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_WEB_PORT", "8765")),
    )


if __name__ == "__main__":
    main()
