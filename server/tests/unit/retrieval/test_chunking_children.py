from __future__ import annotations

from forge.core.types import ChunkMetadata, ChunkType, Element, ElementMetadata, ElementType
from forge.retrieval.chunkers import BaseChunker, ChunkConfig
from forge.retrieval.fusion.base import AggregatedParent
from forge.retrieval.pipeline import ParentChildRetriever
from forge.retrieval.chunkers.strategies.hierarchical import HierarchicalChunker
from forge.retrieval.chunkers.strategies.sliding_window import SlidingWindowChunker


class StaticChunker(BaseChunker):
    def __init__(self, parent_data: dict, config: ChunkConfig):
        super().__init__(config)
        self._parent_data = parent_data

    def _build_parents(self, elements: list[Element]) -> list[dict]:
        _ = elements
        return [self._parent_data]


def _children(chunks):
    return [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]


def test_table_children_keep_complete_rows_and_repeat_header():
    table = "\n".join(
        [
            "统计说明",
            "",
            "| 指标 | 一季度 | 二季度 |",
            "| --- | --- | --- |",
            "| 收入 | 100 | 120 |",
            "| 成本 | 60 | 70 |",
            "| 利润 | 40 | 50 |",
        ]
    )
    chunker = StaticChunker(
        {
            "content": table,
            "header_path": "经营数据",
            "source_type": "table",
            "metadata": ChunkMetadata(),
        },
        ChunkConfig(child_target_chars=72, child_overlap_chars=0),
    )

    children = _children(chunker.chunk([], doc_id="doc"))

    assert len(children) >= 2
    assert all(child.source_type == "table" for child in children)
    assert all(child.metadata.parent_source == "table" for child in children)
    assert all(child.metadata.extra["splitter"] == "table_rows" for child in children)
    for child in children:
        assert "统计说明" in child.content
        assert "| 指标 | 一季度 | 二季度 |" in child.content
        assert "| --- | --- | --- |" in child.content
        for line in child.content.splitlines():
            if "|" in line:
                assert line.strip().startswith("|")
                assert line.strip().endswith("|")

    joined = "\n".join(child.content for child in children)
    assert "| 收入 | 100 | 120 |" in joined
    assert "| 成本 | 60 | 70 |" in joined
    assert "| 利润 | 40 | 50 |" in joined


def test_mixed_children_split_text_and_table_segments_separately():
    content = "\n\n".join(
        [
            "先说明这张表的统计口径。",
            "\n".join(
                [
                    "| 字段 | 含义 |",
                    "| --- | --- |",
                    "| amount | 金额 |",
                    "| currency | 币种 |",
                ]
            ),
            "表格之后还有补充说明。",
        ]
    )
    chunker = StaticChunker(
        {
            "content": content,
            "header_path": "接口字段",
            "source_type": "mixed",
            "metadata": ChunkMetadata(),
        },
        ChunkConfig(child_target_chars=42, child_overlap_chars=0),
    )

    children = _children(chunker.chunk([], doc_id="doc"))

    assert any(child.source_type == "text" for child in children)
    table_children = [child for child in children if child.source_type == "table"]
    assert table_children
    for child in table_children:
        assert child.metadata.parent_source == "mixed"
        assert child.metadata.extra["splitter"] == "table_rows"
        for line in child.content.splitlines():
            if "|" in line:
                assert line.strip().startswith("|")
                assert line.strip().endswith("|")


def test_element_children_use_parser_table_boundaries_and_context():
    table = "\n".join(
        [
            "| 字段 | 含义 |",
            "| --- | --- |",
            "| amount | 金额 |",
            "| currency | 币种 |",
        ]
    )
    context = "这段文字说明下面表格的统计口径。"
    chunker = StaticChunker(
        {
            "content": "\n\n".join([context, table, "表格之后的说明。"]),
            "header_path": "字段说明",
            "source_type": "mixed",
            "elements": [
                Element(type=ElementType.TEXT, content=context),
                Element(type=ElementType.TABLE, content=table),
                Element(type=ElementType.TEXT, content="表格之后的说明。"),
            ],
            "metadata": ChunkMetadata(),
        },
        ChunkConfig(child_target_chars=42, child_overlap_chars=0),
    )

    children = _children(chunker.chunk([], doc_id="doc"))
    text_children = [child for child in children if child.source_type == "text"]
    table_children = [child for child in children if child.source_type == "table"]

    assert text_children
    assert table_children
    assert all(child.metadata.parent_source == "mixed" for child in table_children)
    assert all(child.metadata.extra["table_index"] == 0 for child in table_children)
    assert all(context in child.content for child in table_children)
    assert any("表格之后的说明。" in child.content for child in text_children)


def test_text_children_pack_whole_paragraphs_before_fallback_window():
    paragraphs = [
        "第一段说明系统入口和调用方式。",
        "第二段说明认证流程和失败返回。",
        "第三段说明限流策略和重试建议。",
    ]
    chunker = StaticChunker(
        {
            "content": "\n\n".join(paragraphs),
            "header_path": "接口说明",
            "source_type": "text",
            "metadata": ChunkMetadata(),
        },
        ChunkConfig(child_target_chars=34, child_overlap_chars=0),
    )

    children = _children(chunker.chunk([], doc_id="doc"))

    assert len(children) >= 2
    assert all(child.source_type == "text" for child in children)
    assert all(child.metadata.extra["splitter"] == "paragraph" for child in children)
    for paragraph in paragraphs:
        assert sum(paragraph in child.content for child in children) == 1


def test_sliding_window_parent_overlap_uses_separator():
    chunker = SlidingWindowChunker(
        ChunkConfig(sliding_parent_chars=20, child_target_chars=100, child_overlap_chars=5)
    )
    elements = [
        Element(type=ElementType.TEXT, content="first block alpha"),
        Element(type=ElementType.TEXT, content="second block beta"),
    ]

    parents = chunker._build_parents(elements)

    assert len(parents) == 2
    expected_tail = parents[0]["content"][-5:]
    assert parents[1]["content"].startswith(f"{expected_tail}\n\nsecond block beta")


def test_parent_metadata_keeps_page_range():
    chunker = SlidingWindowChunker(
        ChunkConfig(sliding_parent_chars=200, child_target_chars=200, child_overlap_chars=0)
    )
    elements = [
        Element(
            type=ElementType.TEXT,
            content="第一页内容",
            metadata=ElementMetadata(page_number=1),
        ),
        Element(
            type=ElementType.TEXT,
            content="第三页内容",
            metadata=ElementMetadata(page_number=3),
        ),
    ]

    parents = chunker._build_parents(elements)

    assert len(parents) == 1
    extra = parents[0]["metadata"].extra
    assert extra["page"] == 1
    assert extra["page_start"] == 1
    assert extra["page_end"] == 3


def test_retrieved_parent_exposes_page_range_from_extra():
    parent = AggregatedParent(
        parent_id="p1",
        doc_id="d1",
        fusion_score=0.8,
        hit_child_count=2,
        hit_chunk_ids=["c1", "c2"],
    )

    result = ParentChildRetriever._make_retrieved(
        parent,
        {
            "chunk_id": "p1",
            "document_id": "d1",
            "kb_id": "kb1",
            "content": "content",
            "header_path": "第 1 章",
            "source_type": "text",
            "extra": {"page_start": "2", "page_end": 4},
        },
        rerank_score=None,
    )

    assert result.page == 2
    assert result.page_start == 2
    assert result.page_end == 4
    assert result.metadata["page_start"] == "2"
    assert result.metadata["page_end"] == 4


def test_sliding_window_splits_large_table_parents_by_rows():
    table = "\n".join(
        [
            "| 字段 | 含义 |",
            "| --- | --- |",
            "| amount | 金额 |",
            "| currency | 币种 |",
            "| status | 状态 |",
            "| created_at | 创建时间 |",
        ]
    )
    chunker = SlidingWindowChunker(
        ChunkConfig(
            parent_target_max=58,
            child_target_chars=200,
            child_overlap_chars=0,
            table_context_max_chars=80,
        )
    )

    parents = chunker._build_parents(
        [
            Element(type=ElementType.TEXT, content="接口字段说明"),
            Element(type=ElementType.TABLE, content=table),
        ]
    )

    assert len(parents) > 1
    assert all(parent["source_type"] == "table" for parent in parents)
    assert all("接口字段说明" in parent["content"] for parent in parents)
    assert all("| 字段 | 含义 |" in parent["content"] for parent in parents)
    assert all(parent["metadata"].extra["parent_splitter"] == "table_rows" for parent in parents)
    assert [parent["metadata"].extra["row_start"] for parent in parents] == sorted(
        parent["metadata"].extra["row_start"] for parent in parents
    )


def test_hierarchical_splits_only_oversized_table_parent():
    table = "\n".join(
        [
            "| 指标 | 数值 |",
            "| --- | --- |",
            "| revenue | 100 |",
            "| cost | 60 |",
            "| profit | 40 |",
            "| margin | 40% |",
        ]
    )
    chunker = HierarchicalChunker(
        ChunkConfig(parent_target_max=58, child_target_chars=200, child_overlap_chars=0)
    )

    parents = chunker._materialize_section(
        "经营分析",
        [
            Element(type=ElementType.TEXT, content="下面是核心指标表。"),
            Element(type=ElementType.TABLE, content=table),
            Element(type=ElementType.TEXT, content="表格之后是结论。"),
        ],
    )

    table_parents = [parent for parent in parents if parent["source_type"] == "table"]
    text_parents = [parent for parent in parents if parent["source_type"] == "text"]

    assert len(table_parents) > 1
    assert len(text_parents) == 2
    assert all(parent["header_path"].startswith("经营分析 > 表格 1 行 ") for parent in table_parents)
    assert all(parent["metadata"].extra["parent_splitter"] == "table_rows" for parent in table_parents)
    assert all("下面是核心指标表。" in parent["content"] for parent in table_parents)
    assert any("表格之后是结论。" in parent["content"] for parent in text_parents)
