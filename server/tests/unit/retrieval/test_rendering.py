"""父块回灌渲染 (retrieval/rendering.py) 与 knowledge_search 预算分配单测."""

from __future__ import annotations

from forge.retrieval.base import RetrievedParent
from forge.retrieval.rendering import count_tokens, render_parent_snippet
from forge.tools.builtin.knowledge.knowledge_search import KnowledgeSearchTool


def _parent(
    content: str,
    *,
    source_type: str = "text",
    hit_chunk_ids: list[str] | None = None,
    metadata: dict | None = None,
    chunk_id: str = "p1",
) -> RetrievedParent:
    return RetrievedParent(
        chunk_id=chunk_id,
        document_id="d1",
        kb_id="k1",
        content=content,
        source_type=source_type,
        hit_chunk_ids=hit_chunk_ids or [],
        metadata=metadata or {},
    )


_EXCEL_TABLE = (
    "工作表: Sheet1\n"
    "行范围: 1-8\n"
    "\n"
    "| 名称 | 值 |\n"
    "| --- | --- |\n"
    "| 苹果 | 10 |\n"
    "| 香蕉 | 20 |\n"
    "| 橙子 | 30 |\n"
    "| 葡萄 | 40 |\n"
    "| 西瓜 | 50 |\n"
    "| 芒果 | 60 |\n"
    "| 菠萝 | 70 |\n"
    "| 柠檬 | 80 |"
)


# ----------------------------------------------------------------------
# 文本父块
# ----------------------------------------------------------------------
def test_text_parent_within_budget_returns_whole():
    content = "苹果是一种常见水果，富含维生素。"
    parent = _parent(content)

    result = render_parent_snippet(parent, "苹果", budget_tokens=count_tokens(content) + 50)

    assert result == content  # 整块回灌


def test_text_parent_over_budget_anchors_and_fits():
    content = (
        "开头无关内容。" + "填充段落。" * 80 + "目标关键词的答案就在这一句里。" + "结尾填充。" * 80
    )
    parent = _parent(content)
    budget = max(1, count_tokens(content) // 4)

    result = render_parent_snippet(parent, "目标关键词", budget_tokens=budget)

    assert result != content
    assert count_tokens(result) <= budget
    assert "目标关键词" in result  # 锚定命中点
    assert ("省略" in result) or ("截断" in result)  # 带省略标记


# ----------------------------------------------------------------------
# 表格父块
# ----------------------------------------------------------------------
def test_table_parent_within_budget_returns_whole():
    parent = _parent(_EXCEL_TABLE, source_type="table")

    result = render_parent_snippet(
        parent, "香蕉", budget_tokens=count_tokens(_EXCEL_TABLE) + 50
    )

    assert result == _EXCEL_TABLE  # 整表回灌


def test_excel_table_over_budget_returns_hit_rows_precise():
    parent = _parent(
        _EXCEL_TABLE,
        source_type="table",
        hit_chunk_ids=["c1"],
        metadata={
            "row_indices": [1, 2, 3, 4, 5, 6, 7, 8],
            "sheet_row_count": 8,
            "child_debug_manifest": [{"id": "c1", "row_start": 2, "row_end": 3}],
        },
    )
    budget = count_tokens(_EXCEL_TABLE) - 1  # 强制进入降级路径

    result = render_parent_snippet(parent, "无关query", budget_tokens=budget)

    # 命中的是原始第 2-3 行 (香蕉/橙子)
    assert "香蕉" in result
    assert "橙子" in result
    # 未命中行不应出现
    assert "苹果" not in result
    assert "柠檬" not in result
    # 表头保留 + 全表行数提示
    assert "| 名称 | 值 |" in result
    assert "全表共 8 行" in result
    assert count_tokens(result) <= budget


def test_table_over_budget_lexical_fallback_without_manifest():
    parent = _parent(_EXCEL_TABLE, source_type="table")  # 无 row_indices/manifest
    budget = count_tokens(_EXCEL_TABLE) - 1

    result = render_parent_snippet(parent, "橙子", budget_tokens=budget)

    assert "橙子" in result  # 关键词命中行
    assert "香蕉" not in result
    assert "全表共 8 行" in result


# ----------------------------------------------------------------------
# knowledge_search 预算分配
# ----------------------------------------------------------------------
def test_format_results_drops_tail_when_over_budget():
    results = [
        _parent(f"这是第{n}条片段的正文内容。", chunk_id=f"p{n}") for n in range(1, 6)
    ]

    out = KnowledgeSearchTool._format_results(
        results,
        query="片段",
        total_budget_tokens=1000,
        min_snippet_tokens=400,
    )

    # 1000 // 400 = 2 条, 丢弃 3 条
    assert "省略 3 条" in out
    assert "[1]" in out
    assert "[2]" in out
    assert "[3]" not in out
