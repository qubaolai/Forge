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
