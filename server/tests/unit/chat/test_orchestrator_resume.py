"""TurnOrchestrator resume 并发保护测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from forge.chat.orchestrator import TurnOrchestrator
from forge.chat.resumer import ResumeError
from forge.chat.types import ResumeState, TurnContext


def _ctx() -> TurnContext:
    return TurnContext(
        user_id="u1",
        user_name="",
        session_id="sess_x",
        assistant_msg_id="msg_a",
        user_msg_id="msg_u",
        current_user_message="继续",
        agent_mode="chat",
        is_new_session=False,
        new_title=None,
        trace_id="trace-x",
    )


def _resume_state() -> ResumeState:
    return ResumeState(
        original_user_message="原问题",
        prev_content="部分回答",
        prev_tool_calls=[],
        prev_reasoning_content=None,
        prev_reasoning_duration_ms=None,
        prev_usage={},
        prev_finish_reason="aborted",
        prev_status="aborted",
    )


@pytest.mark.asyncio
async def test_start_resume_register_conflict_returns_resume_error(tmp_path, monkeypatch) -> None:
    """并发 resume 注册冲突应返回可预期的 409 错误。"""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path))
    orchestrator = TurnOrchestrator(cast(Any, SimpleNamespace()))
    orchestrator_any = cast(Any, orchestrator)
    orchestrator_any._resumer = SimpleNamespace(
        prepare=AsyncMock(return_value=(_ctx(), _resume_state()))
    )
    supervisor = SimpleNamespace(register=AsyncMock(side_effect=RuntimeError("busy")))

    with (
        patch("forge.chat.orchestrator.get_chat_supervisor", return_value=supervisor),
        pytest.raises(ResumeError) as exc,
    ):
        await orchestrator_any.start_resume(
            user_id="u1",
            message_id="msg_a",
            trace_id="trace-x",
        )

    assert exc.value.code == "40902"
