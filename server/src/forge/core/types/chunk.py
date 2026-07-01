"""Chunk 数据模型.

Chunk 是切分阶段的输出, 也是进入 embedding 和向量库的标准单元.
采用父子结构: 父块用于命中后返回给 LLM, 子块用于向量检索.
"""

from dataclasses import dataclass, field
from enum import Enum


class ChunkType(str, Enum):
    """Chunk 类型枚举.

    PARENT 用于返回给 LLM 以提供完整上下文, CHILD 用于向量检索以提高精度.
    """

    PARENT = "parent"
    CHILD = "child"


class ChunkStrategy(str, Enum):
    """父块产出策略枚举."""

    HIERARCHICAL = "hierarchical"
    SLIDING_WINDOW = "sliding_window"
    ROW_BASED = "row_based"


@dataclass
class ChunkMetadata:
    """Chunk 附加信息, 替代原本松散的 dict.

    所有字段均为可选, 不同切分路径填充不同子集:
        - 层级父块:    strategy=hierarchical, element_count, has_table
        - 滑窗文本父块: strategy=sliding_window, element_count
        - 滑窗表格父块: strategy=sliding_window, has_context, context_chars
        - 行级表格父块: strategy=row_based, has_table, extra.row_start/row_end
        - 子块:        parent_source

    Attributes:
        strategy:       产出该 chunk 的切分策略.
        element_count:  父块包含的 Element 数量.
        has_table:      父块是否包含表格 (层级聚合时使用).
        has_context:    表格父块是否带前置上下文 (滑窗模式).
        context_chars:  表格前置上下文的字符数 (滑窗模式).
        parent_source:  子块继承的父块 source_type.
        extra:          预留扩展字段, 避免新增需求时反复改类.
    """

    strategy: ChunkStrategy | None = None
    element_count: int | None = None
    has_table: bool | None = None
    has_context: bool | None = None
    context_chars: int | None = None
    parent_source: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Chunk:
    """切分后的文档块.

    Attributes:
        chunk_id: 全局唯一 ID, 格式为 ``{doc_id}__p_xxx`` 或 ``{doc_id}__c_xxx``.
        chunk_type: PARENT 或 CHILD.
        content: 用于 embedding 或返回给 LLM 的文本内容.
        source_type: 内容来源类型, 取值为 text/table/row/image_caption 等.
        header_path: 层级路径字符串, 如 "第2章 系统架构 > 2.1 模块划分".
        parent_id: 子块指向其父块的 chunk_id, 父块此字段为 None.
        doc_id: 所属文档的 ID, 用于增量更新时按文档批量删除.
        doc_version: 文档版本号, 配合 doc_id 支持版本切换.
        chunk_hash: 内容的 MD5 哈希, 用于增量更新时判断是否变化.
        raw_content_ref: 原始内容引用, 如图片的 OSS URL 或表格的原始路径.
        metadata: 结构化附加信息.
    """

    chunk_id: str
    chunk_type: ChunkType
    content: str
    source_type: str
    header_path: str
    parent_id: str | None = None
    doc_id: str = ""  # = kb_documents.id (新 KB 体系)
    kb_id: str = ""  # = knowledge_bases.id, ingest 时由 KbIngestService 注入
    doc_version: str = "v1"  # 已废弃, 保留兼容老 chunker, 不再用于存储 metadata
    chunk_hash: str = ""
    raw_content_ref: str = ""
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)
