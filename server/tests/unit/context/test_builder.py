"""CompositeContextBuilder 单测.

覆盖路径:
    1. 基本拼装: system / history / current user 三段齐全, 顺序正确
    2. exclude_message_ids: 本轮 user_msg 被剔除, 不重复
    3. ORM 行过滤: 空内容 / 非 user|assistant 行被丢
    4. MemoryStore 失败 -> 降级 + 写 meta.degraded, 不抛
    5. enable_summary=False / enable_facts=False -> 跳过且不算降级
    6. facts + summary 全有 -> system message 含两段 markdown 节
    7. history 超 token 预算 -> 从早期丢, 记 history_truncated_by_budget
    8. 历史查询失败 -> 整请求失败 (按设计抛出, 不降级)
    9. 可观测性: tracer.span 收齐所有 BuildMeta 属性
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import pytest

from forge.context.base import (
    AgentContextConfig,
    BuildRequest,
    WorkflowContextLayer,
    WorkspaceContextLayer,
)
from forge.context.builder import CompositeContextBuilder
from forge.llm.token_counter import HeuristicCounter
from forge.memory.base import Fact, FactRecallRequest, MemoryStoreError, Summary
from forge.memory.null import NullMemoryStore
from forge.observability.tracing import tracer as tracer_mod


# ---------------------------------------------------------------------------
# Fakes (不用 mock: fake 更易读, 也能精确反映 ORM 字段访问)
# ---------------------------------------------------------------------------
@dataclass
class FakeRow:
    """模拟 ChatMessage ORM, 只暴露 builder 用到的三个字段."""

    id: str
    role: str
    content: str


class FakeRepo:
    """模拟 MessageRepository.load_recent.

    可配置返回固定行集 / 抛错.
    """

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
    """按构造时给定值返回, 不抛."""

    def __init__(
        self,
        summary: Summary | None = None,
        facts: list[Fact] | None = None,
    ):
        self._summary = summary
        self._facts = facts or []
        self.recall_calls: list[FactRecallRequest] = []

    async def get_summary(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        return self._summary

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        self.recall_calls.append(request)
        return list(self._facts)


class BrokenMemory:
    """get_summary / recall_facts 都抛 MemoryStoreError."""

    async def get_summary(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        raise MemoryStoreError("summary backend down")

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        raise MemoryStoreError("vector backend down")


# ---------------------------------------------------------------------------
# 公共 fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def counter() -> HeuristicCounter:
    # 用 Heuristic 而非全局 get_token_counter() -- 测试不受 tiktoken 是否安装影响
    return HeuristicCounter()


def _agent(**overrides) -> AgentContextConfig:
    base = {
        "system_prompt": "你是助手",
        "context_window": 4096,
        "history_limit": 30,
        "enable_summary": True,
        "enable_facts": True,
        "facts_top_k": 5,
    }
    base.update(overrides)
    return AgentContextConfig(**base)


def _request(**overrides) -> BuildRequest:
    base = {
        "user_id": "u1",
        "session_id": "s1",
        "current_user_message": "当前问题",
        "agent": _agent(),
        "exclude_message_ids": (),
    }
    base.update(overrides)
    return BuildRequest(**base)


# ---------------------------------------------------------------------------
# 1. 基本拼装
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_basic_assembly_order_and_roles(counter):
    repo = FakeRepo(
        [
            FakeRow("m1", "user", "Q1"),
            FakeRow("m2", "assistant", "A1"),
            FakeRow("m3", "user", "Q2"),
            FakeRow("m4", "assistant", "A2"),
        ]
    )
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    res = await builder.build(_request())

    roles = [m.role for m in res.messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert res.messages[0].content == "你是助手"
    # 当前用户消息被 <current_question> 标签包住 (串轮防御)
    assert res.messages[-1].content == "<current_question>\n当前问题\n</current_question>"
    assert res.meta.history_messages_used == 4
    assert res.meta.history_messages_dropped == 0
    assert res.meta.degraded == []
    assert res.meta.estimated_input_tokens > 0
    # load_recent 被调用一次, limit 走 agent.history_limit
    assert repo.calls == [("s1", 30)]


@pytest.mark.asyncio
async def test_context_builder_6_layer_order(counter):
    builder = CompositeContextBuilder(FakeRepo(), NullMemoryStore(), counter)

    res = await builder.build(
        _request(
            workspace_id="ws_a",
            workflow_id="wf_1",
            workspace_context=WorkspaceContextLayer(
                workspace_id="ws_a",
                root_path="/repo",
                assistant_prompt="workspace rule",
                settings={"language": "python"},
            ),
            project_decisions=("use FastAPI",),
            workflow_context=WorkflowContextLayer(
                workflow_id="wf_1",
                template_id="feature_dev",
                mode="heavy",
                role_artifacts={"ra": [{"artifact_id": "art_1"}]},
                recent_events=({"type": "phase.started", "payload": {"role": "developer"}},),
            ),
            role_history=("RA completed SRS",),
        )
    )

    system = res.messages[0].content
    markers = [
        "你是助手",
        "## Workspace Context",
        "## Project Decisions",
        "## Workflow Context",
        "## Role History",
    ]
    assert [system.index(marker) for marker in markers] == sorted(
        system.index(marker) for marker in markers
    )
    assert "workspace rule" in system
    assert "workflow_id: wf_1" in system
    assert res.messages[-1].content == "<current_question>\n当前问题\n</current_question>"


@pytest.mark.asyncio
async def test_workflow_context_layer_includes_recent_events(counter):
    builder = CompositeContextBuilder(FakeRepo(), NullMemoryStore(), counter)
    events = tuple({"type": f"event_{idx}", "payload": {"idx": idx}} for idx in range(6))

    res = await builder.build(
        _request(
            workflow_context=WorkflowContextLayer(
                workflow_id="wf_recent",
                template_id="quick_fix",
                recent_events=events,
            )
        )
    )

    system = res.messages[0].content
    assert "event_0" not in system
    assert "event_1" in system
    assert "event_5" in system


# (test_summary_isolated_by_workspace_id 已挪到 tests/unit/memory/test_workspace_scope.py)


# ---------------------------------------------------------------------------
# 2. exclude_message_ids: 本轮 user_msg 不重复
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exclude_message_ids_drops_current_user_row(counter):
    repo = FakeRepo(
        [
            FakeRow("m1", "user", "上一轮 Q"),
            FakeRow("m2", "assistant", "上一轮 A"),
            FakeRow("CUR", "user", "当前问题"),
        ]
    )
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    res = await builder.build(_request(exclude_message_ids=("CUR",)))

    history_contents = [m.content for m in res.messages[1:-1]]
    # 即使 history 里有原文 "当前问题" 也只是字符串包含, 不是裸值
    assert "<current_question>" not in "".join(history_contents)
    assert history_contents == ["上一轮 Q", "上一轮 A"]
    # current_user_message 作为最后一条 user, 包 <current_question> 标签
    assert res.messages[-1].role == "user"
    assert res.messages[-1].content == "<current_question>\n当前问题\n</current_question>"


# ---------------------------------------------------------------------------
# 3. ORM 行过滤: 空内容 / 非 user/assistant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_filters_empty_and_non_chat_rows(counter):
    repo = FakeRepo(
        [
            FakeRow("m1", "system", "ignored system row"),
            FakeRow("m2", "tool", "ignored tool row"),
            FakeRow("m3", "user", ""),  # 空内容
            FakeRow("m4", "user", "real Q"),
            FakeRow("m5", "assistant", "real A"),
        ]
    )
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    res = await builder.build(_request())

    history = res.messages[1:-1]
    assert [m.content for m in history] == ["real Q", "real A"]
    assert res.meta.history_messages_used == 2


# ---------------------------------------------------------------------------
# 4. MemoryStore 失败 -> 降级
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_failure_degrades_without_raising(counter):
    repo = FakeRepo([FakeRow("m1", "user", "Q")])
    builder = CompositeContextBuilder(repo, BrokenMemory(), counter)

    res = await builder.build(_request())

    assert "summary_fetch_failed" in res.meta.degraded
    assert "facts_recall_failed" in res.meta.degraded
    # 主链路仍然给出可用 messages
    assert res.messages[0].role == "system"
    assert res.messages[-1].role == "user"
    assert res.meta.summary_included is False
    assert res.meta.facts_included == 0


# ---------------------------------------------------------------------------
# 5. enable_summary/enable_facts=False -> 不调 MemoryStore, 不算降级
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_disabled_memory_skips_calls_and_not_degraded(counter):
    repo = FakeRepo([FakeRow("m1", "user", "Q")])
    mem = StaticMemory(
        summary=Summary(
            session_id="s1",
            content="should not appear",
            covered_until_message_id=None,
            token_count=5,
            updated_at=datetime.now(),
        ),
        facts=[Fact(id="f1", user_id="u1", content="should not appear", source="user_manual")],
    )
    builder = CompositeContextBuilder(repo, mem, counter)

    res = await builder.build(_request(agent=_agent(enable_summary=False, enable_facts=False)))

    # MemoryStore 该用例下根本没被调用
    assert mem.recall_calls == []
    # system message 不含 facts / summary 节
    sys_content = res.messages[0].content
    assert "## 关于用户" not in sys_content
    assert "## 早期对话摘要" not in sys_content
    assert res.meta.summary_included is False
    assert res.meta.facts_included == 0
    assert res.meta.degraded == []


# ---------------------------------------------------------------------------
# 6. facts + summary 全有 -> system message 两段渲染
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_system_message_includes_facts_and_summary_sections(counter):
    repo = FakeRepo([FakeRow("m1", "user", "Q")])
    mem = StaticMemory(
        summary=Summary(
            session_id="s1",
            content="早期讨论了 RAG 架构.",
            covered_until_message_id="m0",
            token_count=10,
            updated_at=datetime.now(),
        ),
        facts=[
            Fact(id="f1", user_id="u1", content="偏好 Python", source="llm_extracted"),
            Fact(id="f2", user_id="u1", content="在做 RAG 项目", source="user_manual"),
        ],
    )
    builder = CompositeContextBuilder(repo, mem, counter)

    res = await builder.build(_request())

    sys_content = res.messages[0].content
    # 各段顺序: base_prompt -> facts -> summary
    base_idx = sys_content.find("你是助手")
    facts_idx = sys_content.find("## 关于用户")
    summary_idx = sys_content.find("## 早期对话摘要")
    assert 0 == base_idx < facts_idx < summary_idx
    # facts 列表项
    assert "- 偏好 Python" in sys_content
    assert "- 在做 RAG 项目" in sys_content
    # summary 正文
    assert "早期讨论了 RAG 架构." in sys_content
    # meta 计数
    assert res.meta.summary_included is True
    assert res.meta.facts_included == 2
    # recall 调用参数: query == current_user_message, top_k 来自 agent
    assert len(mem.recall_calls) == 1
    call = mem.recall_calls[0]
    assert call.user_id == "u1"
    assert call.query == "当前问题"
    assert call.top_k == 5


# ---------------------------------------------------------------------------
# 7. token 预算: 历史被裁剪 -> degraded 记录
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_history_truncated_when_over_budget(counter):
    # 构造一组长消息, 配极小 context_window, 强制裁剪
    big = "字" * 400  # heuristic: 400/2 + 1 + 4 (overhead) ≈ 205 token / 条
    repo = FakeRepo(
        [
            FakeRow("m1", "user", big),
            FakeRow("m2", "assistant", big),
            FakeRow("m3", "user", big),
            FakeRow("m4", "assistant", big),
        ]
    )
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    # 总窗口 1000, history_share=0.5 -> history 预算 500, 最多塞下 ~2 条
    res = await builder.build(_request(agent=_agent(context_window=1000)))

    assert res.meta.history_messages_dropped > 0
    assert res.meta.history_messages_used + res.meta.history_messages_dropped == 4
    assert "history_truncated_by_budget" in res.meta.degraded
    # 保留的应是最新的若干条 (从尾向前)
    kept_contents = [m.content for m in res.messages[1:-1]]
    assert all(c == big for c in kept_contents)


# ---------------------------------------------------------------------------
# 8. 历史查询失败 -> 直接抛, 不降级
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_history_failure_propagates(counter):
    repo = FakeRepo(raise_on_load=True)
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    with pytest.raises(RuntimeError, match="db down"):
        await builder.build(_request())


# ---------------------------------------------------------------------------
# 9. 可观测性: build() 产生的 span 含全部 BuildMeta 属性
# ---------------------------------------------------------------------------
class _RecordingSpan:
    def __init__(self) -> None:
        self.attrs: dict = {}
        self.error: BaseException | None = None

    def set(self, key: str, value) -> None:
        self.attrs[key] = value

    def set_error(self, exc: BaseException) -> None:
        self.error = exc


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _RecordingSpan]] = []

    from contextlib import contextmanager

    @contextmanager
    def span(self, name: str, **attrs):
        s = _RecordingSpan()
        s.attrs.update(attrs)
        try:
            yield s
        except Exception as e:  # noqa: BLE001
            s.set_error(e)
            raise
        finally:
            self.spans.append((name, s))


@pytest.fixture
def recording_tracer(monkeypatch):
    rec = _RecordingTracer()
    monkeypatch.setattr(tracer_mod, "_tracer", rec)
    return rec


@pytest.mark.asyncio
async def test_build_emits_span_with_all_meta_attrs(counter, recording_tracer):
    repo = FakeRepo([FakeRow("m1", "user", "Q"), FakeRow("m2", "assistant", "A")])
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    await builder.build(_request())

    assert len(recording_tracer.spans) == 1
    name, s = recording_tracer.spans[0]
    assert name == "context.build"
    # 构造时传入的固定属性
    assert s.attrs["session_id"] == "s1"
    assert s.attrs["user_id"] == "u1"
    assert s.attrs["history_limit"] == 30
    assert s.attrs["context_window"] == 4096
    # build 结束时回填的 BuildMeta 属性
    for key in (
        "estimated_input_tokens",
        "history_messages_used",
        "history_messages_dropped",
        "summary_included",
        "facts_included",
        "degraded",
    ):
        assert key in s.attrs, f"missing span attr: {key}"
    assert s.error is None


# (test_tracer_spans_carry_workflow_and_workspace_id 已挪到
# tests/unit/observability/test_tracer_attrs.py)


@pytest.mark.asyncio
async def test_span_captures_error_when_history_fails(counter, recording_tracer):
    repo = FakeRepo(raise_on_load=True)
    builder = CompositeContextBuilder(repo, NullMemoryStore(), counter)

    with pytest.raises(RuntimeError):
        await builder.build(_request())

    assert len(recording_tracer.spans) == 1
    name, s = recording_tracer.spans[0]
    assert name == "context.build"
    assert isinstance(s.error, RuntimeError)


# ---------------------------------------------------------------------------
# 抑制默认 LoggingTracer 的 INFO 日志 (避免单测输出干扰)
# ---------------------------------------------------------------------------
logging.getLogger("forge.observability.tracing.tracer").setLevel(logging.WARNING)
