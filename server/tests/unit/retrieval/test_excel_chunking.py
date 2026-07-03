from __future__ import annotations

from forge.core.types import ChunkStrategy, ChunkType
from forge.retrieval.chunkers import ChunkConfig, select_chunker
from forge.retrieval.chunkers.strategies.row_based import RowBasedChunker
from forge.retrieval.parsers.excel.csv_parser import CsvParser


def _parse_csv(tmp_path, content: str):
    file_path = tmp_path / "inventory.csv"
    file_path.write_text(content, encoding="utf-8")
    return CsvParser().parse(file_path)


def test_selector_uses_row_based_chunker_for_tabular_elements(tmp_path):
    elements = _parse_csv(tmp_path, "SKU,数量\nA,10\n")

    chunker = select_chunker(elements, ChunkConfig())

    assert isinstance(chunker, RowBasedChunker)


def test_row_based_chunker_keeps_small_sheet_as_single_parent(tmp_path):
    elements = _parse_csv(
        tmp_path,
        "SKU,数量\nA,10\nB,20\nC,30\nD,40\n",
    )
    chunker = RowBasedChunker(
        ChunkConfig(parent_target_max=70, child_target_chars=200, child_overlap_chars=0)
    )

    chunks = chunker.chunk(elements, doc_id="doc")
    parents = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.PARENT]

    assert len(parents) == 1
    assert all(parent.source_type == "table" for parent in parents)
    assert all(parent.metadata.strategy == ChunkStrategy.ROW_BASED for parent in parents)
    assert all("| SKU | 数量 |" in parent.content for parent in parents)
    assert parents[0].metadata.extra["chunking_mode"] == "small_sheet"
    assert parents[0].metadata.extra["parent_splitter"] == "excel_sheet"
    assert "| A | 10 |" in parents[0].content
    assert "| D | 40 |" in parents[0].content


def test_row_based_chunker_does_not_drop_single_row_sheet(tmp_path):
    elements = _parse_csv(tmp_path, "文件名称\n")
    chunker = RowBasedChunker(ChunkConfig(parent_target_max=500, child_target_chars=200))

    chunks = chunker.chunk(elements, doc_id="doc")
    parents = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.PARENT]

    assert len(parents) == 1
    assert "数据行: 0" not in parents[0].content
    assert "| 列1 |" in parents[0].content
    assert "| 文件名称 |" in parents[0].content
    assert parents[0].metadata.extra["row_start"] == 1
    assert parents[0].metadata.extra["row_end"] == 1


def test_small_table_stays_whole_single_child_with_context(tmp_path):
    # 表结构定义类小表: 整表作为一个子块, 保留列头/工作表上下文, 不再拆行
    elements = _parse_csv(
        tmp_path,
        "编号,名称\n1,苹果\n\n2,香蕉\n3,梨\n",
    )
    chunker = RowBasedChunker(ChunkConfig())

    chunks = chunker.chunk(elements, doc_id="doc")
    children = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]

    assert len(children) == 1
    child = children[0]
    assert child.source_type == "table"
    assert child.metadata.extra["splitter"] == "excel_table"
    # 整表内容 + 列头上下文
    assert "| 编号 | 名称 |" in child.content
    assert "苹果" in child.content and "香蕉" in child.content and "梨" in child.content
    # 相对行号映射回真实行号 (2/4/5)
    assert child.metadata.extra["row_start"] == 2
    assert child.metadata.extra["row_end"] == 5


def test_large_table_splits_into_row_groups_not_single_rows(tmp_path):
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 251))]
    elements = _parse_csv(tmp_path, "\n".join(lines))
    chunker = RowBasedChunker(ChunkConfig())

    chunks = chunker.chunk(elements, doc_id="doc")
    parents = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.PARENT]
    children = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]

    # 父块仍是整 sheet; 子块按 table_child_max_chars 拆成完整行组 (远少于 250)
    assert len(parents) == 1
    assert parents[0].metadata.extra["chunking_mode"] == "medium_sheet"
    assert 1 < len(children) < 250
    assert all(child.source_type == "table" for child in children)
    assert all("| 编号 | 名称 |" in child.content for child in children)  # 每组保留列头
    assert children[0].metadata.extra["row_start"] == 2
    assert children[-1].metadata.extra["row_end"] == 251


def test_table_child_budget_is_configurable(tmp_path):
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 21))]  # 20 数据行
    elements = _parse_csv(tmp_path, "\n".join(lines))

    whole = [
        c
        for c in RowBasedChunker(ChunkConfig()).chunk(elements, doc_id="doc")
        if c.chunk_type == ChunkType.CHILD
    ]
    assert len(whole) == 1  # 默认预算下 20 行整表一块

    tiny = [
        c
        for c in RowBasedChunker(ChunkConfig(table_child_max_chars=120)).chunk(
            elements, doc_id="doc"
        )
        if c.chunk_type == ChunkType.CHILD
    ]
    assert len(tiny) > 1  # 调小预算后拆成多个行组
    assert tiny[0].metadata.extra["row_start"] == 2
    assert tiny[-1].metadata.extra["row_end"] == 21


def test_excel_parent_thresholds_are_configurable(tmp_path):
    # 默认 150 行是单个 medium 父块; 调小阈值后应拆成多个行组父块
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 151))]
    elements = _parse_csv(tmp_path, "\n".join(lines))

    default_parents = [
        c
        for c in RowBasedChunker(ChunkConfig()).chunk(elements, doc_id="doc")
        if c.chunk_type == ChunkType.PARENT
    ]
    assert len(default_parents) == 1

    tuned = RowBasedChunker(
        ChunkConfig(excel_sheet_parent_max_rows=100, excel_large_parent_rows=50)
    )
    tuned_parents = [
        c for c in tuned.chunk(elements, doc_id="doc") if c.chunk_type == ChunkType.PARENT
    ]
    assert len(tuned_parents) == 3  # 150 行按每组 50 行拆成 3 个父块
    assert all(p.metadata.extra["chunking_mode"] == "large_sheet_row_group" for p in tuned_parents)


def test_large_sheet_uses_large_row_group_parents(tmp_path):
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 2002))]
    elements = _parse_csv(tmp_path, "\n".join(lines))
    chunker = RowBasedChunker(ChunkConfig(parent_target_max=70, child_target_chars=120))

    chunks = chunker.chunk(elements, doc_id="doc")
    parents = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.PARENT]

    assert len(parents) == 7
    assert all(parent.metadata.extra["chunking_mode"] == "large_sheet_row_group" for parent in parents)
    assert parents[0].metadata.extra["row_start"] == 2
    assert parents[0].metadata.extra["row_end"] == 301
    assert parents[-1].metadata.extra["row_end"] == 2002
