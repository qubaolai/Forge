from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.core.request_context import set_allowed_knowledge_kb_ids
from forge.tools.builtin.knowledge.knowledge_search import (
    KnowledgeSearchTool,
    _as_clean_str_list,
)


def test_knowledge_search_schema_requires_kb_ids() -> None:
    schema = KnowledgeSearchTool().openai_schema()
    params = schema["function"]["parameters"]

    assert params["required"] == ["query", "kb_ids"]
    assert params["properties"]["kb_ids"]["minItems"] == 1
    assert "kb_names" in params["properties"]


def test_clean_str_list_accepts_single_string_for_runtime_compatibility() -> None:
    assert _as_clean_str_list("kb-1") == ["kb-1"]
    assert _as_clean_str_list([" kb-1 ", "", "kb-2"]) == ["kb-1", "kb-2"]
    assert _as_clean_str_list(None) == []


@pytest.mark.asyncio
async def test_knowledge_search_rejects_missing_kb_ids_before_user_context() -> None:
    result = await KnowledgeSearchTool().arun({"query": "付款条款"})

    assert result.startswith("错误: kb_ids 不能为空")


@pytest.mark.asyncio
async def test_knowledge_search_rejects_invalid_top_n_before_db_access() -> None:
    result = await KnowledgeSearchTool().arun(
        {"query": "付款条款", "kb_ids": ["kb-1"], "top_n": "很多"}
    )

    assert result == "错误: top_n 必须是整数"


@pytest.mark.asyncio
async def test_knowledge_search_rejects_when_chat_turn_did_not_select_kb() -> None:
    set_allowed_knowledge_kb_ids([])

    result = await KnowledgeSearchTool().arun(
        {"query": "付款条款", "kb_ids": ["kb-1"]}
    )

    assert result == "错误: 当前对话未选择知识库, 不允许执行 knowledge_search"


@pytest.mark.asyncio
async def test_knowledge_search_rejects_kb_outside_selected_scope() -> None:
    set_allowed_knowledge_kb_ids(["kb-1"])

    result = await KnowledgeSearchTool().arun(
        {"query": "付款条款", "kb_ids": ["kb-2"]}
    )

    assert result == "错误: 请求的知识库不在本轮允许范围内: ['kb-2']"


def test_knowledge_search_returns_whole_parent_within_budget() -> None:
    """父块 token 数在预算内时整块回灌 (small-to-big), 不再截断."""
    content = "开头说明。" * 20 + "付款条款为验收后 30 天内支付。" + "补充说明。" * 20
    result = KnowledgeSearchTool._format_results(
        [_snippet_parent(content)],
        query="付款条款是什么",
        total_budget_tokens=6000,
        min_snippet_tokens=400,
    )

    assert content in result  # 完整父块原样出现
    assert "省略" not in result and "截断" not in result


def test_knowledge_search_anchors_snippet_when_over_budget() -> None:
    """父块超预算时退化为 query 命中锚窗, 保留命中句 + 省略标记."""
    content = "开头说明。" * 160 + "付款条款为验收后 30 天内支付。" + "补充说明。" * 160
    result = KnowledgeSearchTool._format_results(
        [_snippet_parent(content)],
        query="付款条款是什么",
        total_budget_tokens=300,
        min_snippet_tokens=300,
    )

    assert "付款条款为验收后 30 天内支付" in result
    assert ("省略" in result) or ("截断" in result)


def _snippet_parent(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        source_type="text",
        hit_chunk_ids=[],
        metadata={},
        document_name="合同.docx",
        kb_name="合同库",
        header_path="付款",
        page=3,
        page_start=3,
        page_end=3,
        final_score=0.9,
        source_url=None,
        document_id="d1",
        chunk_id="p1",
    )
