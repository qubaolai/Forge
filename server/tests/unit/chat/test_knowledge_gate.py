from __future__ import annotations

import pytest

from forge.agents.lifecycle import StepContext
from forge.chat.knowledge_gate import (
    KnowledgeSearchToolGate,
    _looks_like_kb_request,
    filter_knowledge_search_for_prompt,
)
from forge.core.types.message import ToolCall
from forge.tools.base import Tool


class _FakeTool(Tool):
    parameters = {"type": "object"}

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} tool"

    def run(self, args):
        _ = args
        return "ok"


def _step() -> StepContext:
    return StepContext(step_index=0, max_steps=8, messages_count=2)


def test_kb_intent_requires_resource_and_action_or_direct_phrase() -> None:
    assert _looks_like_kb_request("请在知识库里检索付款条款") is True
    assert _looks_like_kb_request("根据上传的文档总结结论") is True
    assert _looks_like_kb_request("RAG 是什么，KB 是什么") is False
    assert _looks_like_kb_request("解释一下检索算法") is False


def test_prompt_tool_filter_uses_same_kb_intent_gate() -> None:
    tools = [_FakeTool("knowledge_search"), _FakeTool("calculator")]

    hidden = filter_knowledge_search_for_prompt(
        tools,
        user_message="解释一下 RAG 和 KB 的区别",
    )
    visible = filter_knowledge_search_for_prompt(
        tools,
        user_message="请在知识库里检索付款条款",
    )
    no_kb = filter_knowledge_search_for_prompt(
        tools,
        user_message="请在知识库里检索付款条款",
        has_accessible_kbs=False,
    )

    assert [tool.name for tool in hidden] == ["calculator"]
    assert [tool.name for tool in visible] == ["knowledge_search", "calculator"]
    assert [tool.name for tool in no_kb] == ["calculator"]


def test_prompt_tool_filter_can_be_forced_by_frontend_selection() -> None:
    tools = [_FakeTool("knowledge_search"), _FakeTool("calculator")]

    forced_visible = filter_knowledge_search_for_prompt(
        tools,
        user_message="普通问题",
        force_allow=True,
    )
    forced_hidden = filter_knowledge_search_for_prompt(
        tools,
        user_message="请在知识库里检索付款条款",
        force_allow=False,
    )

    assert [tool.name for tool in forced_visible] == ["knowledge_search", "calculator"]
    assert [tool.name for tool in forced_hidden] == ["calculator"]


@pytest.mark.asyncio
async def test_gate_removes_knowledge_schema_without_kb_intent() -> None:
    gate = KnowledgeSearchToolGate(
        tools=[_FakeTool("knowledge_search"), _FakeTool("calculator")],
        user_message="帮我写一个普通回复",
    )

    schemas = await gate.resolve_tools(_step())

    assert schemas is not None
    assert [schema["function"]["name"] for schema in schemas] == ["calculator"]


@pytest.mark.asyncio
async def test_gate_vetoes_stray_knowledge_tool_call() -> None:
    gate = KnowledgeSearchToolGate(
        tools=[_FakeTool("knowledge_search")],
        user_message="今天心情不错，聊聊吧",
    )

    veto = await gate.before_tool_call(
        ToolCall(id="call_1", name="knowledge_search", arguments={"query": "心情"}),
        _step(),
    )

    assert veto is not None
    assert veto.blocked is True
    assert veto.replacement_message is not None
    assert veto.replacement_message.tool_call_id == "call_1"
    assert veto.replacement_message.name == "knowledge_search"


@pytest.mark.asyncio
async def test_gate_allows_explicit_knowledge_search_request() -> None:
    gate = KnowledgeSearchToolGate(
        tools=[_FakeTool("knowledge_search")],
        user_message="请在知识库里检索付款条款",
    )

    schemas = await gate.resolve_tools(_step())
    veto = await gate.before_tool_call(
        ToolCall(id="call_1", name="knowledge_search", arguments={"query": "付款条款"}),
        _step(),
    )

    assert schemas is None
    assert veto is None


@pytest.mark.asyncio
async def test_gate_can_be_disabled_when_user_has_no_accessible_kb() -> None:
    gate = KnowledgeSearchToolGate(
        tools=[_FakeTool("knowledge_search"), _FakeTool("calculator")],
        user_message="请在知识库里检索付款条款",
        knowledge_search_enabled=False,
    )

    schemas = await gate.resolve_tools(_step())
    veto = await gate.before_tool_call(
        ToolCall(id="call_1", name="knowledge_search", arguments={"query": "付款条款"}),
        _step(),
    )

    assert schemas is not None
    assert [schema["function"]["name"] for schema in schemas] == ["calculator"]
    assert veto is not None
    assert veto.blocked is True


@pytest.mark.asyncio
async def test_gate_does_not_veto_other_tools() -> None:
    gate = KnowledgeSearchToolGate(
        tools=[_FakeTool("knowledge_search"), _FakeTool("calculator")],
        user_message="普通数学问题",
    )

    veto = await gate.before_tool_call(
        ToolCall(id="call_2", name="calculator", arguments={"expr": "1+1"}),
        _step(),
    )

    assert veto is None
