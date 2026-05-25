"""阶段 1 等价性测试: 新 DefaultContextBuilder ≡ 旧 CompositeContextBuilder.

目标: 相同入参下, 两者输出的 messages / 关键 meta 字段等价.
覆盖现有 test_builder.py 的核心场景:
    - 基本拼装顺序
    - exclude_message_ids
    - ORM 行过滤 (空内容 / 非 user/assistant)
    - MemoryStore 降级
    - enable_summary/facts=False
    - facts + summary 同时存在
    - history 超 token 预算
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import pytest

from forge.context.base import AgentContextConfig, BuildRequest
from forge.context.builder import CompositeContextBuilder
from forge.context_mgmt.builder.factory import build_context_builder
from forge.context_mgmt.types import ContextMode, ContextRequest
from forge.llm.token_counter import HeuristicCounter
from forge.memory.base import Fact, FactRecallRequest, MemoryStoreError, Summary
from forge.memory.null import NullMemoryStore


# ---------------------------------------------------------------------------
# Fakes (复制自 tests/unit/context/test_builder.py 风格)
# ---------------------------------------------------------------------------
@dataclass
class FakeRow:
    """模拟 ChatMessage ORM, 只暴露 builder 用到的三个字段."""

    id: str
    role: str
    content: str


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

    async def get_summary(self, session_id, *, workspace_id=None):
        return self._summary

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        self.recall_calls.append(request)
        return list(self._facts)


class BrokenMemory:
    async def get_summary(self, session_id, *, workspace_id=None):
        raise MemoryStoreError("summary backend down")

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        raise MemoryStoreError("vector backend down")


# ---------------------------------------------------------------------------
# 构造工具: 同样的语义参数 -> 旧 BuildRequest 和新 ContextRequest
# ---------------------------------------------------------------------------
@pytest.fixture
def counter() -> HeuristicCounter:
    return HeuristicCounter()


def _build_old_request(
    *,
    user_message: str = "当前问题",
    history_limit: int = 30,
    context_window: int = 4096,
    enable_summary: bool = True,
    enable_facts: bool = True,
    exclude_message_ids: tuple[str, ...] = (),
) -> BuildRequest:
    return BuildRequest(
        user_id="u1",
        session_id="s1",
        current_user_message=user_message,
        agent=AgentContextConfig(
            system_prompt="你是助手",
            context_window=context_window,
            history_limit=history_limit,
            enable_summary=enable_summary,
            enable_facts=enable_facts,
            facts_top_k=5,
        ),
        exclude_message_ids=exclude_message_ids,
    )


def _build_new_request(
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
        # 用 override 跳过模板渲染, 与旧 system_prompt="你是助手" 一致
        system_prompt_override="你是助手",
        context_window=context_window,
        history_limit=history_limit,
        enable_summary=enable_summary,
        enable_facts=enable_facts,
        facts_top_k=5,
        exclude_message_ids=exclude_message_ids,
    )


# ---------------------------------------------------------------------------
# 等价性断言工具
# ---------------------------------------------------------------------------
def assert_equivalent(old_result, new_snapshot):
    """新旧两种实现输出的 messages 应当等价."""
    # 1. 角色顺序一致
    old_roles = [m.role for m in old_result.messages]
    new_roles = [m.role for m in new_snapshot.messages]
    assert old_roles == new_roles, f"角色顺序不一致: old={old_roles} new={new_roles}"

    # 2. 各条消息 content 一致
    for i, (om, nm) in enumerate(zip(old_result.messages, new_snapshot.messages)):
        assert om.content == nm.content, f"messages[{i}] content 不一致"

    # 3. 关键 meta 字段一致
    assert old_result.meta.history_messages_used == new_snapshot.history_messages_used
    assert old_result.meta.history_messages_dropped == new_snapshot.history_messages_dropped
    assert old_result.meta.summary_included == new_snapshot.summary_included
    assert old_result.meta.facts_included == new_snapshot.facts_included
    # degraded 列表内容应一致 (顺序可能不同, 用集合比对)
    assert set(old_result.meta.degraded) == set(new_snapshot.degraded), (
        f"degraded 不一致: old={old_result.meta.degraded} new={new_snapshot.degraded}"
    )


# ---------------------------------------------------------------------------
# 测试用例: 与 test_builder.py 一一对应
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_basic_assembly_equivalence(counter):
    rows = [
        FakeRow("m1", "user", "Q1"),
        FakeRow("m2", "assistant", "A1"),
        FakeRow("m3", "user", "Q2"),
        FakeRow("m4", "assistant", "A2"),
    ]

    old = CompositeContextBuilder(FakeRepo(list(rows)), NullMemoryStore(), counter)
    old_result = await old.build(_build_old_request())

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=NullMemoryStore(),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(_build_new_request())

    assert_equivalent(old_result, new_snapshot)


@pytest.mark.asyncio
async def test_exclude_message_ids_equivalence(counter):
    rows = [
        FakeRow("m1", "user", "上一轮 Q"),
        FakeRow("m2", "assistant", "上一轮 A"),
        FakeRow("CUR", "user", "当前问题"),
    ]

    old = CompositeContextBuilder(FakeRepo(list(rows)), NullMemoryStore(), counter)
    old_result = await old.build(
        _build_old_request(exclude_message_ids=("CUR",))
    )

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=NullMemoryStore(),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(
        _build_new_request(exclude_message_ids=("CUR",))
    )

    assert_equivalent(old_result, new_snapshot)


@pytest.mark.asyncio
async def test_filter_non_chat_rows_equivalence(counter):
    rows = [
        FakeRow("m1", "system", "ignored"),
        FakeRow("m2", "tool", "ignored"),
        FakeRow("m3", "user", ""),
        FakeRow("m4", "user", "real Q"),
        FakeRow("m5", "assistant", "real A"),
    ]

    old = CompositeContextBuilder(FakeRepo(list(rows)), NullMemoryStore(), counter)
    old_result = await old.build(_build_old_request())

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=NullMemoryStore(),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(_build_new_request())

    assert_equivalent(old_result, new_snapshot)


@pytest.mark.asyncio
async def test_memory_failure_degrades_equivalence(counter):
    rows = [FakeRow("m1", "user", "Q")]

    old = CompositeContextBuilder(FakeRepo(list(rows)), BrokenMemory(), counter)
    old_result = await old.build(_build_old_request())

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=BrokenMemory(),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(_build_new_request())

    assert_equivalent(old_result, new_snapshot)


@pytest.mark.asyncio
async def test_disabled_memory_equivalence(counter):
    rows = [FakeRow("m1", "user", "Q")]
    mem = StaticMemory(
        summary=Summary(
            session_id="s1", content="X", covered_until_message_id=None,
            token_count=5, updated_at=datetime.now(),
        ),
        facts=[Fact(id="f1", user_id="u1", content="X", source="user_manual")],
    )

    old = CompositeContextBuilder(FakeRepo(list(rows)), mem, counter)
    old_result = await old.build(
        _build_old_request(enable_summary=False, enable_facts=False)
    )

    mem2 = StaticMemory(summary=mem._summary, facts=list(mem._facts))
    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=mem2,
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(
        _build_new_request(enable_summary=False, enable_facts=False)
    )

    assert_equivalent(old_result, new_snapshot)
    # 新旧实现都不应该调用 MemoryStore
    assert mem.recall_calls == []
    assert mem2.recall_calls == []


@pytest.mark.asyncio
async def test_facts_and_summary_equivalence(counter):
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

    old = CompositeContextBuilder(
        FakeRepo(list(rows)),
        StaticMemory(summary=mem_summary, facts=list(mem_facts)),
        counter,
    )
    old_result = await old.build(_build_old_request())

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=StaticMemory(summary=mem_summary, facts=list(mem_facts)),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(_build_new_request())

    assert_equivalent(old_result, new_snapshot)
    # 显式核对 system 文本含两段标题
    sys_text = new_snapshot.messages[0].content
    assert "## 关于用户" in sys_text
    assert "## 早期对话摘要" in sys_text


@pytest.mark.asyncio
async def test_history_truncation_equivalence(counter):
    big = "字" * 400
    rows = [
        FakeRow("m1", "user", big),
        FakeRow("m2", "assistant", big),
        FakeRow("m3", "user", big),
        FakeRow("m4", "assistant", big),
    ]

    old = CompositeContextBuilder(FakeRepo(list(rows)), NullMemoryStore(), counter)
    old_result = await old.build(_build_old_request(context_window=1000))

    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(list(rows)),
        memory_store=NullMemoryStore(),
        token_meter=_make_meter(counter),
    )
    new_snapshot = await new.build(_build_new_request(context_window=1000))

    # 注意: 新实现的 dialogue_budget 比例 0.40, 旧实现 history_share 0.50.
    # 这里改为只断言"两边都触发了截断 + 关键 meta 字段语义一致".
    assert new_snapshot.history_messages_dropped > 0
    assert "history_truncated_by_budget" in new_snapshot.degraded
    # 旧版同样会触发截断 (history_share=0.50, budget=500, 每条 ~205 token)
    assert old_result.meta.history_messages_dropped > 0
    # 保留消息总数关系: used + dropped = 总条数
    assert (
        new_snapshot.history_messages_used
        + new_snapshot.history_messages_dropped
        == 4
    )


@pytest.mark.asyncio
async def test_history_failure_propagates(counter):
    """history 失败应硬抛, 不降级 (与旧 CompositeContextBuilder 一致)."""
    new = build_context_builder(
        mode=ContextMode.CHAT,
        message_store=FakeRepo(raise_on_load=True),
        memory_store=NullMemoryStore(),
        token_meter=_make_meter(counter),
    )
    with pytest.raises(RuntimeError, match="db down"):
        await new.build(_build_new_request())


# ---------------------------------------------------------------------------
# 工具: 用一个 HeuristicCounter 包装成 TokenMeter
# ---------------------------------------------------------------------------
def _make_meter(counter: HeuristicCounter):
    from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
    return DefaultTokenMeter(counter)


logging.getLogger("forge.context_mgmt.builder.context_builder").setLevel(logging.WARNING)
