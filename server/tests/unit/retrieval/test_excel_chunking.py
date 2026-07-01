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


def test_row_based_children_are_row_level_key_value(tmp_path):
    elements = _parse_csv(
        tmp_path,
        "编号,名称\n1,苹果\n\n2,香蕉\n3,梨\n",
    )
    chunker = RowBasedChunker(ChunkConfig(parent_target_max=500))

    chunks = chunker.chunk(elements, doc_id="doc")
    children = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]

    # 行级: 每个数据行一个子块 (真实行号 2/4/5, 第 3 行为空被跳过)
    assert len(children) == 3
    assert all(child.source_type == "table" for child in children)
    assert all(child.metadata.extra["splitter"] == "excel_row_kv" for child in children)
    assert children[0].metadata.extra["row_start"] == 2
    assert children[-1].metadata.extra["row_end"] == 5
    assert any(4 in child.metadata.extra["row_indices"] for child in children)
    # 键值化内容 (列名: 值), 不再是 markdown 表格
    assert "编号: 1" in children[0].content
    assert "名称: 苹果" in children[0].content
    assert all("|" not in child.content for child in children)


def test_medium_sheet_single_parent_with_row_level_children(tmp_path):
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 251))]
    elements = _parse_csv(tmp_path, "\n".join(lines))
    chunker = RowBasedChunker(ChunkConfig(parent_target_max=70))

    chunks = chunker.chunk(elements, doc_id="doc")
    parents = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.PARENT]
    children = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]

    # 父块仍是整 sheet (供 P1-3 整表/命中行组回灌), 子块降到行级
    assert len(parents) == 1
    assert parents[0].metadata.extra["chunking_mode"] == "medium_sheet"
    assert len(children) == 250
    assert all(child.metadata.extra["row_count"] == 1 for child in children)
    assert children[0].metadata.extra["row_start"] == 2
    assert children[-1].metadata.extra["row_end"] == 251


def test_excel_child_rows_config_groups_rows(tmp_path):
    lines = ["编号,名称", *(f"{i},项目{i}" for i in range(1, 26))]  # 25 数据行
    elements = _parse_csv(tmp_path, "\n".join(lines))
    chunker = RowBasedChunker(ChunkConfig(excel_child_rows=10))

    chunks = chunker.chunk(elements, doc_id="doc")
    children = [chunk for chunk in chunks if chunk.chunk_type == ChunkType.CHILD]

    # 每组 10 行 → 3 个子块 (10/10/5)
    assert [child.metadata.extra["row_count"] for child in children] == [10, 10, 5]
    assert children[0].metadata.extra["row_start"] == 2
    assert children[0].metadata.extra["row_end"] == 11
    assert children[0].content.count("编号:") == 10


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
