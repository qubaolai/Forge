"""Summarizer 单测.

覆盖:
    1. 空 messages -> 空字符串, 不调 LLM
    2. 仅 system / 空内容 messages -> 空字符串, 不调 LLM
    3. 正常 messages -> 调 gateway.complete, 返回 content (strip 过)
    4. gateway.complete 抛错 -> 返回空字符串, 不抛
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.core.types.message import Message
from forge.llm import LLMResponse
from forge.memory.summary.summarizer import Summarizer


def _gateway(content: str = "summary text", *, raise_exc: Exception | None = None):
    """构造 mock LLMGateway: gateway.complete 是 AsyncMock."""
    gw = MagicMock()
    if raise_exc:
        gw.complete = AsyncMock(side_effect=raise_exc)
    else:
        gw.complete = AsyncMock(
            return_value=LLMResponse(content=content, model="test", usage={})
        )
    return gw


@pytest.mark.asyncio
async def test_empty_messages_returns_empty_without_llm() -> None:
    gw = _gateway()
    s = Summarizer(gw)
    assert await s.summarize([]) == ""
    gw.complete.assert_not_called()


@pytest.mark.asyncio
async def test_only_system_or_empty_messages_returns_empty() -> None:
    gw = _gateway()
    s = Summarizer(gw)
    msgs = [
        Message(role="system", content="x"),
        Message(role="user", content=""),
        Message(role="assistant", content=""),
    ]
    assert await s.summarize(msgs) == ""
    gw.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summarize_returns_llm_content_stripped() -> None:
    gw = _gateway("  好的摘要  \n")
    s = Summarizer(gw)
    msgs = [
        Message(role="user", content="问 1"),
        Message(role="assistant", content="答 1"),
    ]
    assert await s.summarize(msgs) == "好的摘要"
    gw.complete.assert_called_once()


@pytest.mark.asyncio
async def test_summarize_llm_failure_returns_empty() -> None:
    gw = _gateway(raise_exc=RuntimeError("llm 503"))
    s = Summarizer(gw)
    msgs = [Message(role="user", content="x"), Message(role="assistant", content="y")]
    assert await s.summarize(msgs) == ""


# ---------------------------------------------------------------------------
# 增量滚动: previous_summary 模板分支 (走真实 PromptRegistry 渲染)
# ---------------------------------------------------------------------------
def _sent_prompt(gw) -> str:
    """取 gateway.complete 收到的 LLMRequest 的 prompt 文本."""
    req = gw.complete.call_args.args[0]
    return req.messages[0].content


@pytest.mark.asyncio
async def test_summarize_with_previous_summary_renders_in_prompt() -> None:
    gw = _gateway()
    s = Summarizer(gw)
    msgs = [Message(role="user", content="继续"), Message(role="assistant", content="好")]
    await s.summarize(msgs, previous_summary="既有摘要: 用户在调研 RAG")

    prompt = _sent_prompt(gw)
    assert "既有摘要: 用户在调研 RAG" in prompt
    assert "整体替换" in prompt  # 增量指令块已渲染


@pytest.mark.asyncio
async def test_summarize_without_previous_summary_omits_block() -> None:
    gw = _gateway()
    s = Summarizer(gw)
    msgs = [Message(role="user", content="问"), Message(role="assistant", content="答")]
    await s.summarize(msgs)

    prompt = _sent_prompt(gw)
    assert "既有摘要" not in prompt
