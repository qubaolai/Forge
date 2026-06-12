"""SummaryService 增量滚动摘要单测 (P0-A).

覆盖:
    1. 有旧摘要 -> 按水位 load_after, 不走 load_recent
    2. 有旧摘要 -> Summarizer 收到 previous_summary
    3. 有旧摘要但水位后无新消息 -> 不调 LLM, 不 upsert, 返回 None (幂等)
    4. 无旧摘要 -> 走 load_recent 全量路径, previous_summary 为 None

复用 test_summarize_task._Ctx 的全套 patch.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from forge.memory.base import Summary
from forge.memory.summary.service import get_summary_service

from .test_summarize_task import _Ctx, _row


def _previous(covered_until: str = "m2") -> Summary:
    return Summary(
        session_id="sess_inc",
        content="既有摘要: 用户在调研 RAG",
        covered_until_message_id=covered_until,
        token_count=10,
        updated_at=datetime(2026, 6, 1),
        version=1,
    )


@pytest.mark.asyncio
async def test_incremental_uses_load_after_with_watermark() -> None:
    rows = [
        _row("m3", "user", "继续说说 chunking"),
        _row("m4", "assistant", "chunking 可以这样..."),
    ]
    with _Ctx(rows=rows, llm_summary="融合后的新摘要", previous=_previous("m2")) as ctx:
        result = await get_summary_service().summarize_session("sess_inc")

    # 走增量路径: load_after(水位), 不调 load_recent
    ctx.load_after_mock.assert_awaited_once()
    args = ctx.load_after_mock.await_args.args
    assert args[0] == "sess_inc"
    assert args[1] == "m2"
    ctx.load_recent_mock.assert_not_awaited()
    # 水位推进到增量最后一条
    assert ctx.upsert_mock.call_args.kwargs["covered_until_message_id"] == "m4"
    assert result is not None


@pytest.mark.asyncio
async def test_incremental_passes_previous_summary_to_llm() -> None:
    rows = [
        _row("m3", "user", "继续"),
        _row("m4", "assistant", "好的..."),
    ]
    with _Ctx(rows=rows, previous=_previous()) as ctx:
        await get_summary_service().summarize_session("sess_inc")

    summarize_call = next(c for c in ctx.summarizer_calls if "messages" in c)
    assert summarize_call["previous_summary"] == "既有摘要: 用户在调研 RAG"


@pytest.mark.asyncio
async def test_incremental_no_new_messages_skips_llm() -> None:
    # 水位后无新消息: load_after 返回空
    with _Ctx(rows=[], previous=_previous()) as ctx:
        result = await get_summary_service().summarize_session("sess_inc")

    assert result is None
    assert ctx.upsert_mock.call_count == 0
    # Summarizer.summarize 未被调 (summarizer_calls 里没有 messages 记录)
    assert not any("messages" in c for c in ctx.summarizer_calls)


@pytest.mark.asyncio
async def test_full_path_without_previous_summary() -> None:
    rows = [
        _row("m1", "user", "你好"),
        _row("m2", "assistant", "你好!"),
    ]
    with _Ctx(rows=rows, previous=None) as ctx:
        await get_summary_service().summarize_session("sess_new")

    ctx.load_recent_mock.assert_awaited_once()
    ctx.load_after_mock.assert_not_awaited()
    summarize_call = next(c for c in ctx.summarizer_calls if "messages" in c)
    assert summarize_call["previous_summary"] is None
