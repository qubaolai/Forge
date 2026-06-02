"""HistoryProvider × digest 接线集成测试: 缓存命中走无损 digest, 未命中走降级."""

from __future__ import annotations

import pytest

from forge.context_mgmt.digest.policy import DigestPolicy
from forge.context_mgmt.digest.types import DigestRecord, Segment
from forge.context_mgmt.filters.recent import RecentFilter
from forge.context_mgmt.protocols import TokenMeter
from forge.context_mgmt.providers.history import HistoryProvider
from forge.context_mgmt.tool_policy.verbatim import VerbatimPolicy
from forge.context_mgmt.types import ContextMode, ContextRequest
from forge.core.types.message import Message


class _CharMeter(TokenMeter):
    def count_text(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages)


class _FakeRow:
    def __init__(self, rid: str, role: str, content: str, status: str = "done") -> None:
        self.id = rid
        self.role = role
        self.content = content
        self.status = status


class _FakeMessageStore:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def load_recent(self, session_id: str, limit: int = 30):
        return self._rows


class _FakeDigestStore:
    def __init__(self, mapping: dict[str, DigestRecord]) -> None:
        self._m = mapping

    async def batch_get(self, ids: list[str]) -> dict[str, DigestRecord]:
        return {k: v for k, v in self._m.items() if k in ids}


def _request() -> ContextRequest:
    return ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="继续",
        mode=ContextMode.CHAT,
        history_limit=30,
    )


def _provider(rows, digest_store) -> HistoryProvider:
    return HistoryProvider(
        _FakeMessageStore(rows),
        RecentFilter(),
        VerbatimPolicy(),
        _CharMeter(),
        digest_policy=DigestPolicy(),
        digest_cap=50,
        digest_store=digest_store,
    )


@pytest.mark.asyncio
async def test_cache_hit_uses_lossless_digest():
    long_content = "\n".join(f"line {i}" for i in range(500))
    rows = [
        _FakeRow("m_long", "assistant", long_content),
        _FakeRow("m_short", "user", "hi"),
    ]
    record = DigestRecord(
        ref="msg:m_long",
        total_tokens=9999,
        segments=(
            Segment(kind="prose", start_line=1, end_line=500,
                    digest_text="无损摘要内容XYZ", anchor=None),
        ),
    )
    provider = _provider(rows, _FakeDigestStore({"m_long": record}))

    [chunk] = await provider.provide(_request())
    contents = [m.content for m in chunk.messages]
    long_out = next(c for c in contents if "[ref:msg:m_long]" in c)

    assert "无损摘要内容XYZ" in long_out      # 命中 -> 用无损 digest
    assert "首尾摘录" not in long_out          # 没走廉价截断降级 (该短语是降级专有)
    assert "hi" in contents                    # 短消息原样保留
    assert chunk.degraded == []                # 无损路径不计降级
    assert chunk.info == ["digest_substituted"]  # 走信息通道而非降级通道


@pytest.mark.asyncio
async def test_cache_miss_falls_back_to_truncation_and_flags_pending():
    long_content = "\n".join(f"line {i}" for i in range(500))
    rows = [_FakeRow("m_long", "assistant", long_content)]
    provider = _provider(rows, _FakeDigestStore({}))  # 空缓存

    [chunk] = await provider.provide(_request())
    out = chunk.messages[0].content
    assert "[ref:msg:m_long]" in out
    assert "已折叠" in out                      # 廉价截断降级
    assert chunk.degraded == ["digest_pending"]
    assert chunk.info == []                     # 未命中无 substituted 信息标记
