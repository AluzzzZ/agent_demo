from __future__ import annotations

from fastapi.testclient import TestClient

from minimal_agent.llm import DashScopeLLM
from minimal_agent.runtime import AgentRuntime
from minimal_agent.storage import SessionStore
from minimal_agent.tools import create_default_registry
from minimal_agent.tracing import TraceRecorder
from minimal_agent.web import create_app

from .fakes import FakeSearchProvider, FakeWeatherProvider, ScriptedLLM, text_response


def test_two_accounts_sessions_and_traces_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_1_USERNAME", "alice")
    monkeypatch.setenv("DEMO_USER_1_PASSWORD", "alice123")
    monkeypatch.setenv("DEMO_USER_1_DISPLAY_NAME", "Alice")
    monkeypatch.setenv("DEMO_USER_2_USERNAME", "bob")
    monkeypatch.setenv("DEMO_USER_2_PASSWORD", "bob12345")
    monkeypatch.setenv("DEMO_USER_2_DISPLAY_NAME", "Bob")

    store = SessionStore(tmp_path / "web.db")
    tracer = TraceRecorder(tmp_path / "web-traces.jsonl")

    def runtime_factory() -> AgentRuntime:
        return AgentRuntime(
            llm=ScriptedLLM([text_response("这是隔离会话里的回答。")]),
            store=store,
            tools=create_default_registry(
                search_provider=FakeSearchProvider(),
                weather_provider=FakeWeatherProvider(),
            ),
            tracer=tracer,
        )

    app = create_app(store=store, tracer=tracer, runtime_factory=runtime_factory)
    client = TestClient(app)

    alice_token = _login(client, "alice", "alice123")
    bob_token = _login(client, "bob", "bob12345")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    created = client.post(
        "/api/sessions", json={"title": "天气窗口"}, headers=alice_headers
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    chat = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "上海天气如何？"},
        headers=alice_headers,
    )
    assert chat.status_code == 200
    trace_id = chat.json()["trace_id"]

    assert client.get("/api/sessions", headers=bob_headers).json() == []
    assert (
        client.get(
            f"/api/sessions/{session_id}/messages", headers=bob_headers
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/traces/{trace_id}", headers=bob_headers).status_code
        == 404
    )

    alice_sessions = client.get("/api/sessions", headers=alice_headers).json()
    assert alice_sessions[0]["title"] == "上海天气如何？"
    traces = client.get(
        f"/api/sessions/{session_id}/traces", headers=alice_headers
    ).json()
    assert traces[0]["trace_id"] == trace_id
    assert traces[0]["status"] == "final_answer"
    assert client.delete(
        f"/api/sessions/{session_id}", headers=bob_headers
    ).status_code == 404
    deleted = client.delete(
        f"/api/sessions/{session_id}", headers=alice_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_trace_events"] > 0
    assert client.get("/api/sessions", headers=alice_headers).json() == []
    assert client.get(f"/api/traces/{trace_id}", headers=alice_headers).status_code == 404


def test_logout_revokes_browser_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_1_USERNAME", "alice")
    monkeypatch.setenv("DEMO_USER_1_PASSWORD", "alice123")
    monkeypatch.setenv("DEMO_USER_2_USERNAME", "bob")
    monkeypatch.setenv("DEMO_USER_2_PASSWORD", "bob12345")
    app = create_app(
        store=SessionStore(tmp_path / "auth.db"),
        tracer=TraceRecorder(tmp_path / "auth-traces.jsonl"),
    )
    client = TestClient(app)
    token = _login(client, "alice", "alice123")
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_default_accounts_and_login_page_copy(tmp_path, monkeypatch):
    for name in (
        "DEMO_USER_1_USERNAME",
        "DEMO_USER_1_PASSWORD",
        "DEMO_USER_1_DISPLAY_NAME",
        "DEMO_USER_2_USERNAME",
        "DEMO_USER_2_PASSWORD",
        "DEMO_USER_2_DISPLAY_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(
        store=SessionStore(tmp_path / "defaults.db"),
        tracer=TraceRecorder(tmp_path / "defaults-traces.jsonl"),
    )
    client = TestClient(app)

    page = client.get("/")
    styles = client.get("/static/styles.css?v=test")
    script = client.get("/static/app.js?v=test")
    assert page.status_code == 200
    assert styles.status_code == 200
    assert script.status_code == 200
    assert '<meta name="color-scheme" content="light"' in page.text
    assert "Alice · alice" not in page.text
    assert "Bob · bob" not in page.text
    assert "两个本地用户拥有完全独立" not in page.text
    assert "演示账户的密码由服务端环境变量配置" not in page.text
    assert "Agent Loop" not in page.text
    assert "最多 8 轮" not in page.text
    assert ".chat-area { min-height: 0;" in styles.text
    assert ".workspace { min-width: 0; min-height: 0; height: 100%; overflow: hidden;" in styles.text
    assert "function renderMarkdown(container, source)" in script.text
    assert ".markdown-table-wrap" in styles.text
    assert "session-delete" in script.text
    assert _login(client, "zsw1", "123456")
    assert _login(client, "zsw2", "123456")


def test_create_app_loads_dotenv_before_building_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=test-only-key\nDASHSCOPE_MODEL=test-model\n",
        encoding="utf-8",
    )

    app = create_app(
        store=SessionStore(tmp_path / "dotenv.db"),
        tracer=TraceRecorder(tmp_path / "dotenv-traces.jsonl"),
    )
    runtime = app.state.runtime_factory()

    assert isinstance(runtime.llm, DashScopeLLM)


def test_web_runtime_initialization_failure_creates_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_1_USERNAME", "zsw1")
    monkeypatch.setenv("DEMO_USER_1_PASSWORD", "123456")
    monkeypatch.setenv("DEMO_USER_2_USERNAME", "zsw2")
    monkeypatch.setenv("DEMO_USER_2_PASSWORD", "123456")
    store = SessionStore(tmp_path / "failure.db")
    tracer = TraceRecorder(tmp_path / "failure-traces.jsonl")

    def broken_runtime_factory():
        raise RuntimeError("provider is unavailable")

    client = TestClient(
        create_app(
            store=store,
            tracer=tracer,
            runtime_factory=broken_runtime_factory,
        )
    )
    token = _login(client, "zsw1", "123456")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/api/sessions", json={"title": "失败记录"}, headers=headers
    ).json()["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "测试失败 Trace"},
        headers=headers,
    )
    traces = client.get(
        f"/api/sessions/{session_id}/traces", headers=headers
    ).json()

    assert response.status_code == 503
    assert "Trace ID:" in response.json()["detail"]
    assert traces[0]["status"] == "failed"
    assert tracer.read_events(
        user_id="demo-user-1", trace_id=traces[0]["trace_id"]
    )[-1]["phase"] == "runtime_or_model"


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["token"]
