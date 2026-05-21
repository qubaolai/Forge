"""Summarizer 单测.

覆盖:
    1. 空 messages -> 空字符串, 不调 LLM
    2. 仅 system / 空内容 messages -> 空字符串, 不调 LLM
    3. 正常 messages -> 调 LLM, 返回 content (strip 过)
    4. LLM 抛错 -> 返回空字符串, 不抛
"""

from __future__ import annotations

from unittest.mock import MagicMock

from forge.core.types.message import Message
from forge.llm.providers.base import ChatResult
from forge.memory.summary.summarizer import Summarizer


def _llm(content: str = "summary text", *, raise_exc: Exception | None = None):
    llm = MagicMock()
    if raise_exc:
        llm.chat.side_effect = raise_exc
    else:
        llm.chat.return_value = ChatResult(content=content, model="test", usage={})
    return llm


def test_empty_messages_returns_empty_without_llm() -> None:
    llm = _llm()
    s = Summarizer(llm)
    assert s.summarize([]) == ""
    llm.chat.assert_not_called()


def test_only_system_or_empty_messages_returns_empty() -> None:
    llm = _llm()
    s = Summarizer(llm)
    msgs = [
        Message(role="system", content="x"),
        Message(role="user", content=""),
        Message(role="assistant", content=""),
    ]
    assert s.summarize(msgs) == ""
    llm.chat.assert_not_called()


def test_summarize_returns_llm_content_stripped() -> None:
    llm = _llm("  好的摘要  \n")
    s = Summarizer(llm)
    msgs = [
        Message(role="user", content="问 1"),
        Message(role="assistant", content="答 1"),
    ]
    assert s.summarize(msgs) == "好的摘要"
    llm.chat.assert_called_once()


def test_summarize_llm_failure_returns_empty() -> None:
    llm = _llm(raise_exc=RuntimeError("llm 503"))
    s = Summarizer(llm)
    msgs = [Message(role="user", content="x"), Message(role="assistant", content="y")]
    assert s.summarize(msgs) == ""
