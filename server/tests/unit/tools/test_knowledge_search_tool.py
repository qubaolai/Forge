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


def test_knowledge_search_formats_query_focused_snippet() -> None:
    content = "开头说明。" * 160 + "付款条款为验收后 30 天内支付。" + "补充说明。" * 160
    result = KnowledgeSearchTool._format_results(
        [
            SimpleNamespace(
                content=content,
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
        ],
        query="付款条款是什么",
    )

    assert "付款条款为验收后 30 天内支付" in result
    assert "...(前文已省略)" in result
    assert "...(后文已截断)" in result
