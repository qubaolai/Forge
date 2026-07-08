"""TurnOrchestrator 与 ContextManager 的上下文编排契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from forge.chat.orchestrator import TurnOrchestrator
from forge.chat.types import ResumeState, RunResult, TurnContext
from forge.context_mgmt.types import (
    CompactionResult,
    ContextSnapshot,
    ContextUsage,
    WindowBudget,
)
from forge.core.types.message import Message


def _ctx() -> TurnContext:
    return TurnContext(
        user_id="u1",
        user_name="用户",
        session_id="s1",
        assistant_msg_id="a1",
        user_msg_id="u1",
        current_user_message="问题",
        agent_mode="chat",
        is_new_session=False,
        new_title=None,
        trace_id="",
        exclude_message_ids=("u1",),
        context_window=4096,
    )


def _snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        messages=[
            Message(role="system", content="system"),
            Message(role="user", content="<current_question>\n问题\n</current_question>"),
        ],
        budget=WindowBudget(4096, 0, 0, 0),
        usage=ContextUsage(4096, 100, 3996, 100 / 4096),
        rendered_system_prompt="rendered prompt",
        rebuild_count=1,
    )


class _Run:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.abort_event = SimpleNamespace()

    async def emit(self, event: dict) -> None:
        self.events.append(event)


class _Runner:
    def __init__(self) -> None:
        self.result = RunResult(content="done")

    async def run(self, ctx, messages, abort_event):
        if False:
            yield None


@pytest.mark.asyncio
async def test_new_turn_preserves_compaction_event_order_and_prompt() -> None:
    snapshot = _snapshot()

    class _ContextManager:
        async def build(self, request, **kwargs):
            await kwargs["on_compaction_started"](snapshot)
            await kwargs["on_compaction_done"](
                CompactionResult(success=True, tokens_saved=25),
                snapshot,
            )
            return snapshot

    orchestrator = TurnOrchestrator(cast(Any, _ContextManager()))
    orchestrator_any = cast(Any, orchestrator)
    orchestrator_any._finalizer = SimpleNamespace(finalize=AsyncMock(return_value=None))
    orchestrator_any._setup_runner = AsyncMock(return_value=_Runner())
    run = _Run()
    body = SimpleNamespace(model_options=SimpleNamespace(model_dump=lambda: {}))

    with patch(
        "forge.chat.orchestrator.get_settings",
        return_value=SimpleNamespace(memory=SimpleNamespace(enabled=True)),
    ):
        await orchestrator_any._execute_new_turn(run, _ctx(), body)

    assert [event["type"] for event in run.events] == [
        "message_start",
        "compaction_started",
        "compaction_done",
        "context_usage",
        "context_meta",
    ]
    assert orchestrator_any._setup_runner.await_args.args[1] == "rendered prompt"


@pytest.mark.asyncio
async def test_new_turn_disables_compaction_when_memory_is_disabled() -> None:
    manager = SimpleNamespace(build=AsyncMock(return_value=_snapshot()))
    orchestrator = TurnOrchestrator(cast(Any, manager))
    orchestrator_any = cast(Any, orchestrator)
    orchestrator_any._finalizer = SimpleNamespace(finalize=AsyncMock(return_value=None))
    orchestrator_any._setup_runner = AsyncMock(return_value=_Runner())
    body = SimpleNamespace(model_options=SimpleNamespace(model_dump=lambda: {}))

    with patch(
        "forge.chat.orchestrator.get_settings",
        return_value=SimpleNamespace(memory=SimpleNamespace(enabled=False)),
    ):
        await orchestrator_any._execute_new_turn(_Run(), _ctx(), body)

    assert manager.build.await_args.kwargs["allow_compaction"] is False


@pytest.mark.asyncio
async def test_resume_never_allows_active_compaction() -> None:
    manager = SimpleNamespace(build=AsyncMock(return_value=_snapshot()))
    orchestrator = TurnOrchestrator(cast(Any, manager))
    orchestrator_any = cast(Any, orchestrator)
    orchestrator_any._finalizer = SimpleNamespace(finalize=AsyncMock(return_value=None))
    orchestrator_any._setup_runner = AsyncMock(return_value=_Runner())
    prev = ResumeState(
        original_user_message="问题",
        prev_content="",
        prev_tool_calls=[],
        prev_reasoning_content=None,
        prev_reasoning_duration_ms=None,
        prev_usage={},
        prev_finish_reason="aborted",
        prev_status="aborted",
    )

    await orchestrator_any._execute_resume(_Run(), _ctx(), prev)

    assert manager.build.await_args.kwargs == {"allow_compaction": False}
    assert orchestrator_any._setup_runner.await_args.args[1] == "rendered prompt"
