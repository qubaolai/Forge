"""DefaultContextBuilder 场景测试（原迁移期等价性测试，旧 builder 已删除后转为新内核单测）。

覆盖核心构建场景:
    - 基本拼装顺序
    - exclude_message_ids
    - ORM 行过滤 (空内容 / 非 user/assistant)
    - MemoryStore 降级 (软失败)
    - enable_summary/facts=False -> 不调用 MemoryStore
    - facts + summary 同时存在
    - history 超 token 预算 -> 截断
    - history 失败 -> 硬抛
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import pytest

from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.types import ContextMode, ContextRequest
from forge.llm.token_counter import HeuristicCounter
from forge.memory.base import Fact, FactRecallRequest, MemoryStoreError, Summary
from forge.memory.null import NullMemoryStore


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
@dataclass
class FakeRow:
    """模拟 ChatMessage ORM, 只暴露 builder 用到的三个字段."""

    id: str
    role: str
    content: str
    parent_id: str | None = None


class FakeRepo:
    """模拟 MessageRepository.load_recent."""

    def __init__(self, rows: list[FakeRow] | None = None, *, raise_on_load: bool = False):
        self.rows = rows or []
        self.raise_on_load = raise_on_load
        self.calls: list[tuple[str, int]] = []

    async def load_recent(self, session_id: str, limit: int):
        self.calls.append((session_id, limit))
        if self.raise_on_load:
            raise RuntimeError("db down")
        return self.rows


class StaticMemory:
    def __init__(
        self,
        summary: Summary | None = None,
        facts: list[Fact] | None = None,
    ):
        self._summary = summary
        self._facts = facts or []
        self.recall_calls: list[FactRecallRequest] = []

    async def get_summary(self, session_id):
        return self._summary

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        self.recall_calls.append(request)
        return list(self._facts)


class BrokenMemory:
    async def get_summary(self, session_id):
        raise MemoryStoreError("summary backend down")

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        raise MemoryStoreError("vector backend down")


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------
@pytest.fixture
def counter() -> HeuristicCounter:
    return HeuristicCounter()


def _make_meter(counter: HeuristicCounter):
    from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
    return DefaultTokenMeter(counter)


def _new_builder(rows_or_repo, memory_store, counter):
    repo = rows_or_repo if isinstance(rows_or_repo, FakeRepo) else FakeRepo(list(rows_or_repo))
    return build_context_builder(
        mode=ContextMode.CHAT,
        message_store=repo,
        memory_store=memory_store,
        token_meter=_make_meter(counter),
    )


def _request(
    *,
    user_message: str = "当前问题",
    history_limit: int = 30,
    context_window: int = 4096,
    enable_summary: bool = True,
    enable_facts: bool = True,
    exclude_message_ids: tuple[str, ...] = (),
) -> ContextRequest:
    return ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message=user_message,
        mode=ContextMode.CHAT,
        system_prompt_override="你是助手",
        context_window=context_window,
        history_limit=history_limit,
        enable_summary=enable_summary,
        enable_facts=enable_facts,
        facts_top_k=5,
        exclude_message_ids=exclude_message_ids,
    )


def _roles(snapshot) -> list[str]:
    return [m.role for m in snapshot.messages]


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_basic_assembly(counter):
    rows = [
        FakeRow("m1", "user", "Q1"),
        FakeRow("m2", "assistant", "A1"),
        FakeRow("m3", "user", "Q2"),
        FakeRow("m4", "assistant", "A2"),
    ]
    builder = _new_builder(rows, NullMemoryStore(), counter)
    snap = await builder.build(_request())

    # system + 4 history + current
    assert _roles(snap) == ["system", "user", "assistant", "user", "assistant", "user"]
    assert "当前问题" in snap.messages[-1].content
    assert snap.history_messages_used == 4
    assert snap.history_messages_dropped == 0
    assert snap.summary_included is False
    assert snap.facts_included == 0


@pytest.mark.asyncio
async def test_exclude_message_ids(counter):
    rows = [
        FakeRow("m1", "user", "上一轮 Q"),
        FakeRow("m2", "assistant", "上一轮 A"),
        FakeRow("CUR", "user", "当前问题"),
    ]
    builder = _new_builder(rows, NullMemoryStore(), counter)
    snap = await builder.build(_request(exclude_message_ids=("CUR",)))

    # CUR 被排除 -> history 仅剩 2 条
    assert snap.history_messages_used == 2
    assert "当前问题" in snap.messages[-1].content


@pytest.mark.asyncio
async def test_filter_non_chat_rows(counter):
    rows = [
        FakeRow("m1", "system", "ignored"),
        FakeRow("m2", "tool", "ignored"),
        FakeRow("m3", "user", ""),
        FakeRow("m4", "user", "real Q"),
        FakeRow("m5", "assistant", "real A"),
    ]
    builder = _new_builder(rows, NullMemoryStore(), counter)
    snap = await builder.build(_request())

    # 仅保留 2 条有效历史 (real Q / real A)
    assert snap.history_messages_used == 2
    contents = [m.content for m in snap.messages]
    assert "ignored" not in contents
    assert "" not in contents[1:]  # 除 system 外无空内容


def test_history_rows_group_user_and_assistant_into_same_turn():
    from forge.context_mgmt.providers.history import HistoryProvider

    rows = [
        FakeRow("u1", "user", "Q1"),
        FakeRow("a1", "assistant", "A1", parent_id="u1"),
        FakeRow("u2", "user", "Q2"),
        FakeRow("a2", "assistant", "A2", parent_id="u2"),
    ]

    messages = HistoryProvider._rows_to_history_messages(rows, set())  # noqa: SLF001

    assert [m.turn_index for m in messages] == [0, 0, 1, 1]


def test_history_rows_treat_orphan_assistant_as_separate_turn():
    from forge.context_mgmt.providers.history import HistoryProvider

    rows = [
        FakeRow("u1", "user", "Q1"),
        FakeRow("a_orphan", "assistant", "A?"),
        FakeRow("u2", "user", "Q2"),
    ]

    messages = HistoryProvider._rows_to_history_messages(rows, set())  # noqa: SLF001

    assert [m.turn_index for m in messages] == [0, 1, 2]


@pytest.mark.asyncio
async def test_memory_failure_degrades(counter):
    rows = [FakeRow("m1", "user", "Q")]
    builder = _new_builder(rows, BrokenMemory(), counter)
    snap = await builder.build(_request())

    # memory 两路都失败 -> 软降级, 不抛; summary/facts 均未带入
    assert snap.degraded  # 非空
    assert snap.summary_included is False
    assert snap.facts_included == 0
    # 构建仍成功: 至少有 system + history + current
    assert _roles(snap)[0] == "system"
    assert snap.history_messages_used == 1
    assert "当前问题" in snap.messages[-1].content


@pytest.mark.asyncio
async def test_disabled_memory_not_consulted(counter):
    rows = [FakeRow("m1", "user", "Q")]
    mem = StaticMemory(
        summary=Summary(
            session_id="s1", content="X", covered_until_message_id=None,
            token_count=5, updated_at=datetime.now(),
        ),
        facts=[Fact(id="f1", user_id="u1", content="X", source="user_manual")],
    )
    builder = _new_builder(rows, mem, counter)
    snap = await builder.build(_request(enable_summary=False, enable_facts=False))

    assert snap.summary_included is False
    assert snap.facts_included == 0
    assert mem.recall_calls == []


@pytest.mark.asyncio
async def test_facts_and_summary(counter):
    rows = [FakeRow("m1", "user", "Q")]
    mem_facts = [
        Fact(id="f1", user_id="u1", content="偏好 Python", source="llm_extracted"),
        Fact(id="f2", user_id="u1", content="在做 RAG 项目", source="user_manual"),
    ]
    mem_summary = Summary(
        session_id="s1",
        content="早期讨论了 RAG 架构.",
        covered_until_message_id="m0",
        token_count=10,
        updated_at=datetime.now(),
    )
    builder = _new_builder(
        rows, StaticMemory(summary=mem_summary, facts=list(mem_facts)), counter
    )
    snap = await builder.build(_request())

    assert snap.summary_included is True
    assert snap.facts_included == 2
    sys_text = snap.messages[0].content
    assert "## 关于用户" in sys_text
    assert "## 早期对话摘要" in sys_text


@pytest.mark.asyncio
async def test_history_truncation(counter):
    big = "字" * 400
    rows = [
        FakeRow("m1", "user", big),
        FakeRow("m2", "assistant", big),
        FakeRow("m3", "user", big),
        FakeRow("m4", "assistant", big),
    ]
    builder = _new_builder(rows, NullMemoryStore(), counter)
    snap = await builder.build(_request(context_window=1000))

    assert snap.history_messages_dropped > 0
    assert "history_truncated_by_budget" in snap.degraded
    assert snap.history_messages_used + snap.history_messages_dropped == 4


@pytest.mark.asyncio
async def test_history_failure_propagates(counter):
    """history 失败应硬抛, 不降级."""
    builder = _new_builder(FakeRepo(raise_on_load=True), NullMemoryStore(), counter)
    with pytest.raises(RuntimeError, match="db down"):
        await builder.build(_request())


logging.getLogger("forge.context_mgmt.builder.context_builder").setLevel(logging.WARNING)
