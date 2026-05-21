"""TurnFinalizer 终态分支单测.

覆盖四条路径:
    stop        -> done       事件, status=done, publish turn.completed
    error       -> error      事件, status=error, 不 publish
    aborted     -> task_partial, status=aborted, 不 publish
    partial_*   -> task_partial, status=partial, 不 publish
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from forge.chat.finalizer import TurnFinalizer
from forge.chat.types import RunResult, TurnContext
from forge.context.base import BuildMeta


def _ctx() -> TurnContext:
    return TurnContext(
        user_id="u1",
        user_name="u",
        session_id="sess_x",
        assistant_msg_id="msg_a",
        user_msg_id="msg_u",
        current_user_message="hi",
        agent_id=None,
        agent_mode="react",
        is_new_session=False,
        new_title=None,
        trace_id="",
        context_window=8192,
    )


async def _collect(finalizer, ctx, result, meta):
    """跑 finalize generator, 收齐 yielded events."""
    events = []
    async for ev in finalizer.finalize(ctx, result, meta):
        events.append(ev)
    return events


def _patches(*, update_message_mock: AsyncMock, publish_mock: AsyncMock):
    return [
        patch.object(TurnFinalizer, "_update_message", update_message_mock),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish_mock),
    ]


# ---------------------------------------------------------------------------
# stop -> done + publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stop_yields_done_and_publishes() -> None:
    finalizer = TurnFinalizer()
    update = AsyncMock()
    publish = AsyncMock()
    result = RunResult(finish_reason="stop", content="最终答复", usage={"total_tokens": 50})
    with (
        patch.object(TurnFinalizer, "_update_message", update),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish),
    ):
        events = await _collect(finalizer, _ctx(), result, BuildMeta())

    assert len(events) == 1
    assert events[0].type == "done"
    assert events[0].to_dict()["finish_reason"] == "stop"
    publish.assert_awaited_once()
    # DB status=done
    args, kwargs = update.await_args
    assert args[-1] == "done" or kwargs.get("status") == "done"


# ---------------------------------------------------------------------------
# error -> error event, no publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_error_yields_error_no_publish() -> None:
    finalizer = TurnFinalizer()
    update = AsyncMock()
    publish = AsyncMock()
    result = RunResult(finish_reason="error", content="part", error_message="LLM 503")
    with (
        patch.object(TurnFinalizer, "_update_message", update),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish),
    ):
        events = await _collect(finalizer, _ctx(), result, BuildMeta())

    assert events[0].type == "error"
    assert "LLM 503" in events[0].to_dict()["message"]
    publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# aborted -> task_partial event, no publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_aborted_yields_task_partial_no_publish() -> None:
    finalizer = TurnFinalizer()
    update = AsyncMock()
    publish = AsyncMock()
    result = RunResult(finish_reason="aborted", content="部分内容", tool_calls=[{"id": "tc1"}])
    with (
        patch.object(TurnFinalizer, "_update_message", update),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish),
    ):
        events = await _collect(finalizer, _ctx(), result, BuildMeta())

    assert events[0].type == "task_partial"
    payload = events[0].to_dict()
    assert payload["reason"] == "aborted"
    assert payload["resumable"] is True
    assert payload["content_so_far"] == "部分内容"
    assert payload["tool_calls_so_far"] == [{"id": "tc1"}]
    publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# partial_steps -> task_partial event, no publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partial_steps_yields_task_partial_no_publish() -> None:
    finalizer = TurnFinalizer()
    update = AsyncMock()
    publish = AsyncMock()
    result = RunResult(finish_reason="partial_steps", content="进展到这")
    with (
        patch.object(TurnFinalizer, "_update_message", update),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish),
    ):
        events = await _collect(finalizer, _ctx(), result, BuildMeta())

    assert events[0].type == "task_partial"
    payload = events[0].to_dict()
    assert payload["reason"] == "partial_steps"
    assert payload["resumable"] is True
    publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# RunResult helper 属性
# ---------------------------------------------------------------------------
def test_run_result_resumable_flags() -> None:
    assert RunResult(finish_reason="stop").is_resumable is False
    assert RunResult(finish_reason="error").is_resumable is False
    assert RunResult(finish_reason="aborted").is_resumable is True
    assert RunResult(finish_reason="partial_steps").is_resumable is True
    assert RunResult(finish_reason="partial_tokens").is_resumable is True
    assert RunResult(finish_reason="partial_timeout").is_resumable is True


def test_run_result_terminal_ok_only_stop() -> None:
    assert RunResult(finish_reason="stop").is_terminal_ok is True
    assert RunResult(finish_reason="length").is_terminal_ok is False
    assert RunResult(finish_reason="partial_steps").is_terminal_ok is False
    assert RunResult(finish_reason="aborted").is_terminal_ok is False
    assert RunResult(finish_reason="error").is_terminal_ok is False
