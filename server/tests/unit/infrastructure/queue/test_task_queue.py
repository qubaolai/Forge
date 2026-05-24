"""TaskQueue 单测.

覆盖:
    1. NullTaskQueue.submit 不抛, 只记日志
    2. init_task_queue(enabled=False) -> NullTaskQueue
    3. init_task_queue(enabled=True) 默认 -> LocalTaskQueue
    4. backend=celery 且 broker_url="" -> NullTaskQueue
    4. init_task_queue 是幂等的, 多次调用拿同一实例
    5. get_task_queue() 在未 init 时返回 NullTaskQueue (脚本 / 单测友好)
    6. CeleryTaskQueue.submit 走 app.send_task (用 mock)
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from unittest.mock import MagicMock

import pytest
from forge.config.paths import tasks_db_path

from forge.infrastructure.queue import (
    LocalTaskQueue,
    NullTaskQueue,
    get_task_queue,
    init_task_queue,
    reset_task_queue,
)
from forge.infrastructure.queue.celery_queue import CeleryTaskQueue


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_task_queue()
    yield
    reset_task_queue()


def test_null_queue_submit_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    q = NullTaskQueue()
    with caplog.at_level("INFO"):
        q.submit("memory.summarize", session_id="s1")
    assert any("NullTaskQueue.submit" in r.message for r in caplog.records)


def test_init_returns_null_when_disabled() -> None:
    q = init_task_queue(enabled=False, broker_url="redis://x", backend_url=None)
    assert isinstance(q, NullTaskQueue)


def test_init_returns_local_by_default() -> None:
    q = init_task_queue(enabled=True, broker_url="", backend_url=None)
    assert isinstance(q, LocalTaskQueue)


def test_init_returns_null_when_celery_without_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSISTANT_TASK_QUEUE_BACKEND", "celery")
    q = init_task_queue(enabled=True, broker_url="", backend_url=None)
    assert isinstance(q, NullTaskQueue)


def test_init_is_idempotent() -> None:
    q1 = init_task_queue(enabled=False, broker_url="", backend_url=None)
    q2 = init_task_queue(enabled=True, broker_url="redis://x", backend_url=None)
    # 第二次 init 不重建; 仍是第一次的实例
    assert q1 is q2


def test_get_before_init_returns_null() -> None:
    q = get_task_queue()
    assert isinstance(q, NullTaskQueue)


def test_celery_task_queue_calls_send_task() -> None:
    app = MagicMock()
    q = CeleryTaskQueue(app)
    q.submit("memory.summarize", session_id="s1", turn_count=10)
    app.send_task.assert_called_once_with(
        "memory.summarize",
        kwargs={"session_id": "s1", "turn_count": 10},
    )


@pytest.mark.asyncio
async def test_local_task_queue_runs_registered_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    called = {"ok": False}

    async def _handler(*, session_id: str) -> None:
        if session_id == "s1":
            called["ok"] = True

    q = LocalTaskQueue(
        handler_resolver=lambda name: _handler if name == "memory.summarize" else None
    )
    q.submit("memory.summarize", session_id="s1")
    await asyncio.sleep(0.1)
    assert called["ok"] is True


@pytest.mark.asyncio
async def test_local_task_queue_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    state = {"calls": 0}

    async def _handler(*, session_id: str) -> None:  # noqa: ARG001
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("boom")

    q = LocalTaskQueue(
        handler_resolver=lambda name: _handler if name == "memory.summarize" else None,
        max_attempts=3,
        retry_delay_seconds=0.01,
    )
    q.submit("memory.summarize", session_id="s1")
    await asyncio.sleep(0.2)

    assert state["calls"] == 2
    conn = sqlite3.connect(tasks_db_path())
    status, attempts = conn.execute(
        "SELECT status, attempts FROM local_tasks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert status == "succeeded"
    assert attempts == 1


@pytest.mark.asyncio
async def test_local_task_queue_marks_dead_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))

    async def _handler(*, session_id: str) -> None:  # noqa: ARG001
        raise RuntimeError("always fail")

    q = LocalTaskQueue(
        handler_resolver=lambda name: _handler if name == "memory.summarize" else None,
        max_attempts=2,
        retry_delay_seconds=0.01,
    )
    q.submit("memory.summarize", session_id="s1")
    await asyncio.sleep(0.2)

    conn = sqlite3.connect(tasks_db_path())
    status, attempts = conn.execute(
        "SELECT status, attempts FROM local_tasks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert status == "dead"
    assert attempts == 2


@pytest.mark.asyncio
async def test_local_task_queue_recovers_running_task_on_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    db_path = tasks_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            run_at REAL NOT NULL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO local_tasks(
            task_name, payload_json, status, attempts, max_attempts, run_at, created_at, updated_at
        ) VALUES (?, ?, 'running', 0, 3, ?, ?, ?)
        """,
        ("memory.summarize", '{"session_id":"s1"}', time.time(), time.time(), time.time()),
    )
    conn.commit()
    conn.close()

    called = {"ok": False}

    async def _handler(*, session_id: str) -> None:
        if session_id == "s1":
            called["ok"] = True

    LocalTaskQueue(handler_resolver=lambda name: _handler if name == "memory.summarize" else None)
    await asyncio.sleep(0.2)
    assert called["ok"] is True
