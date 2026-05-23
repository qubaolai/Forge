"""ContextAssembler 主动压缩单测.

覆盖:
    1. should_compact: 历史未截断 + tokens 远低于阈值 -> False
    2. should_compact: history_messages_dropped > 0 -> True
    3. should_compact: tokens 超阈值 -> True
    4. should_compact: memory.enabled=False -> 永远 False
    5. compact_and_reassemble: SummaryService 抛 InfrastructureError -> 用 prev,
       degraded 加 "compaction_failed"
    6. compact_and_reassemble: 正常路径 -> rebuild + 标记 compaction_performed
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.chat.assembler import ContextAssembler
from forge.chat.types import TurnContext
from forge.context.base import AssembledContext, BuildMeta


def _ctx(context_window: int = 8192) -> TurnContext:
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
        context_window=context_window,
    )


def _result(*, est_tokens: int = 100, dropped: int = 0) -> AssembledContext:
    return AssembledContext(
        messages=[],
        meta=BuildMeta(
            estimated_input_tokens=est_tokens,
            history_messages_dropped=dropped,
        ),
    )


def _settings(memory_enabled: bool = True) -> MagicMock:
    s = MagicMock()
    s.memory.enabled = memory_enabled
    return s


# ---------------------------------------------------------------------------
# should_compact
# ---------------------------------------------------------------------------
def test_should_compact_low_usage_no_drop_returns_false() -> None:
    asm = ContextAssembler()
    with patch(
        "forge.chat.assembler.get_settings",
        return_value=_settings(memory_enabled=True),
    ):
        assert asm.should_compact(_result(est_tokens=1000, dropped=0), _ctx(8192)) is False


def test_should_compact_history_dropped_returns_true() -> None:
    asm = ContextAssembler()
    with patch(
        "forge.chat.assembler.get_settings",
        return_value=_settings(memory_enabled=True),
    ):
        assert asm.should_compact(_result(est_tokens=100, dropped=3), _ctx(8192)) is True


def test_should_compact_token_threshold_returns_true() -> None:
    asm = ContextAssembler(compaction_threshold=0.5)
    with patch(
        "forge.chat.assembler.get_settings",
        return_value=_settings(memory_enabled=True),
    ):
        # 5000 / 8192 ≈ 0.61 > 0.5
        assert asm.should_compact(_result(est_tokens=5000, dropped=0), _ctx(8192)) is True


def test_should_compact_memory_disabled_returns_false() -> None:
    asm = ContextAssembler()
    with patch(
        "forge.chat.assembler.get_settings",
        return_value=_settings(memory_enabled=False),
    ):
        # 即使 history 被丢了, 没有 SummaryStore 也压缩不了
        assert asm.should_compact(_result(est_tokens=10000, dropped=10), _ctx(8192)) is False


# ---------------------------------------------------------------------------
# compact_and_reassemble: 失败路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compact_failure_falls_back_to_prev() -> None:
    from forge.memory.summary.service import InfrastructureError

    asm = ContextAssembler()
    prev = _result(est_tokens=5000, dropped=2)

    fake_service = MagicMock()
    fake_service.summarize_session = AsyncMock(side_effect=InfrastructureError("db"))
    with patch(
        "forge.memory.summary.service.get_summary_service",
        return_value=fake_service,
    ):
        new_result, prompt, saved = await asm.compact_and_reassemble(_ctx(), prev)

    assert new_result is prev
    assert saved == 0
    assert "compaction_failed" in new_result.meta.degraded


# ---------------------------------------------------------------------------
# compact_and_reassemble: 正常路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compact_success_marks_meta_and_rebuilds() -> None:
    asm = ContextAssembler()
    prev = _result(est_tokens=5000, dropped=2)

    fake_service = MagicMock()
    fake_service.summarize_session = AsyncMock(return_value=None)

    new = _result(est_tokens=2000, dropped=0)

    async def fake_build_once(ctx, system_prompt):
        return new

    with (
        patch(
            "forge.memory.summary.service.get_summary_service",
            return_value=fake_service,
        ),
        patch.object(asm, "_build_once", side_effect=fake_build_once),
        patch.object(asm, "_render_system_prompt", return_value="prompt"),
    ):
        new_result, prompt, saved = await asm.compact_and_reassemble(_ctx(), prev)

    assert new_result is new
    assert prompt == "prompt"
    assert saved == 3000  # 5000 - 2000
    assert new_result.meta.compaction_performed is True
    assert new_result.meta.compaction_token_saved == 3000
    assert new_result.meta.rebuild_count == 1
    assert "active_compaction_triggered" in new_result.meta.degraded
