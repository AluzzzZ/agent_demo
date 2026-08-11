from __future__ import annotations

import os

from minimal_agent.env import load_project_env


def test_load_project_env_reads_supported_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# provider configuration",
                "TEST_AGENT_PROVIDER=dashscope",
                'TEST_AGENT_MODEL="deepseek-v4-pro"',
                "export TEST_AGENT_BASE_URL=https://example.test/v1 # comment",
                "INVALID-KEY=ignored",
            ]
        ),
        encoding="utf-8",
    )
    for key in (
        "TEST_AGENT_PROVIDER",
        "TEST_AGENT_MODEL",
        "TEST_AGENT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    loaded = load_project_env(env_file)

    assert loaded == env_file.resolve()
    assert os.environ["TEST_AGENT_PROVIDER"] == "dashscope"
    assert os.environ["TEST_AGENT_MODEL"] == "deepseek-v4-pro"
    assert os.environ["TEST_AGENT_BASE_URL"] == "https://example.test/v1"
    assert "INVALID-KEY" not in os.environ


def test_load_project_env_preserves_existing_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_AGENT_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("TEST_AGENT_MODEL", "from-process")

    load_project_env(env_file)

    assert os.environ["TEST_AGENT_MODEL"] == "from-process"


def test_load_project_env_defaults_to_current_directory(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "TEST_AGENT_AUTO_LOAD=enabled\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TEST_AGENT_AUTO_LOAD", raising=False)

    loaded = load_project_env()

    assert loaded == (tmp_path / ".env").resolve()
    assert os.environ["TEST_AGENT_AUTO_LOAD"] == "enabled"
