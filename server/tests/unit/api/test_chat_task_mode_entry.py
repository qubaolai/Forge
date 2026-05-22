"""M5: chat 入口 task 模式测试。"""

from __future__ import annotations

import subprocess

from config.settings import reset_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge.api.routes.v1 import chat


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat.router, prefix="/chat")
    return app


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    with open(f"{path}/README.md", "w", encoding="utf-8") as f:
        f.write("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_chat_completions_task_mode_emits_run_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "assistant_home"))
    reset_settings()

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _init_git_repo(str(workspace))

    client = TestClient(_app())
    with client.stream(
        "POST",
        "/chat/completions",
        json={
            "message": "修复订单并发扣库存问题",
            "mode": "task",
            "task_options": {"workspace_path": str(workspace)},
        },
    ) as resp:
        payload = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "run.created" in payload
    assert "plan.created" in payload
    assert "task.completed" in payload
    assert "run.completed" in payload
    assert "run.done" in payload


def test_chat_completions_auto_with_task_options_requires_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "assistant_home"))
    reset_settings()

    client = TestClient(_app())
    with client.stream(
        "POST",
        "/chat/completions",
        json={
            "message": "修复登录 bug",
            "mode": "auto",
            "task_options": {},
        },
    ) as resp:
        payload = "".join(resp.iter_text())

    assert resp.status_code == 200
    assert "missing_workspace_path" in payload
