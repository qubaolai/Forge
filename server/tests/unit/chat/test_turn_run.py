"""ChatTurnRun 异常兜底测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from forge.chat.turn_run import TERMINAL_ERROR, ChatTurnRun


class _FakeStore:
    """内存事件存储，避免单测写真实 chat_runs 目录。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.state: dict = {}

    async def save_state(self, state: dict) -> None:
        self.state = dict(state)

    async def update_state(self, **patches):
        self.state.update(patches)
        return self.state

    async def append_event(self, event: dict) -> dict:
        record = {"seq": len(self.events) + 1, **event}
        self.events.append(record)
        return record


@pytest.mark.asyncio
async def test_unhandled_exception_marks_message_error(tmp_path, monkeypatch) -> None:
    """执行协程崩溃时应触发业务消息兜底落库回调。"""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path))
    mark_error = AsyncMock()
    run = ChatTurnRun(
        message_id="msg_a",
        session_id="sess_x",
        user_id="u1",
        on_unhandled_error=mark_error,
    )
    run.store = _FakeStore()

    async def _boom() -> str:
        raise RuntimeError("上下文构建失败")

    run.attach_task(_boom())
    status = await run.wait_done()

    assert status == TERMINAL_ERROR
    mark_error.assert_awaited_once_with("上下文构建失败")
    assert run.store.events[-1]["type"] == "error"
