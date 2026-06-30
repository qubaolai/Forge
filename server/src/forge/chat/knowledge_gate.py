"""Chat 知识库工具 gate.

目的:
    knowledge_search 是只读工具, 但仍会消耗检索/embedding/rerank 资源, 也可能
    因模型自行选择 KB 名称而扩大检索范围。正式方案应由前端传入用户显式选择
    的 kb_ids; 在此之前, 先用本轮用户问题做轻量 gating。
"""

from __future__ import annotations

from collections.abc import Iterable

from forge.agents.lifecycle import NoopLifecycle, StepContext, ToolCallVeto
from forge.core.types.message import Message, ToolCall
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry


_KNOWLEDGE_TOOL = "knowledge_search"

_KB_INTENT_MARKERS = (
    "知识库",
    "资料库",
    "查资料",
    "查询资料",
    "检索",
    "召回",
    "引用",
    "来源",
    "根据资料",
    "根据文档",
    "上传的文档",
    "上传文件",
    "kb",
    "rag",
)


class KnowledgeSearchToolGate(NoopLifecycle):
    """没有明确知识库意图时, 从本轮工具 schema 中移除 knowledge_search。"""

    def __init__(self, *, tools: Iterable[Tool] | None, user_message: str) -> None:
        tool_list = list(tools) if tools is not None else list(ToolRegistry.get_all())
        self._schemas_without_knowledge = [
            tool.openai_schema()
            for tool in tool_list
            if tool.name != _KNOWLEDGE_TOOL
        ]
        self._allow_knowledge_search = _looks_like_kb_request(user_message)

    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        if self._allow_knowledge_search:
            return None
        return self._schemas_without_knowledge

    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        if self._allow_knowledge_search or tc.name != _KNOWLEDGE_TOOL:
            return None
        return ToolCallVeto(
            blocked=True,
            reason="当前用户问题没有明确知识库检索意图",
            replacement_message=Message(
                role="tool",
                content=(
                    "[tool blocked] 当前问题未明确要求查询知识库；"
                    "请直接回答，或先询问用户是否需要检索知识库。"
                ),
                tool_call_id=tc.id,
                name=tc.name,
            ),
        )


def _looks_like_kb_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in _KB_INTENT_MARKERS)

