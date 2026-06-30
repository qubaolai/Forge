"""切分层基类.

职责分工:
    - 子类实现 _build_parents (Element → 父块字典列表)
    - 基类统一处理:
        * 父块/子块的 chunk_id 生成
        * 父块全文 hash 计算
        * 子块滑窗派生 (重叠 + 防断句)
        * parent_id 绑定

子块派生说明:
    采用按字符数的滑动窗口, 保留与父块的字符级重叠. 不做按句子或段落
    的语义分割, 因为父块本身已经是语义单元 (一个 H 节或一段连续文本),
    子块只是为了让向量检索更细粒度, 切碎一点不影响命中后用父块返回.

# TODO 配置类应该统一收敛到配置config中
# TODO(retrieval-layer): 以下事项不在 chunking 层处理, 在 retrieval/prompt
# 组装层完成:
#   1. 子块向量化时, 在 content 前拼接 header_path, 让标题语义参与检索.
#      例如: f"{header_path}\n\n{chunk.content}" 再喂给 embedding.
#   2. 父块返回给 LLM 前, 拼接 header_path 作为前缀, 告诉 LLM 当前内容
#      所在的章节边界.
#   3. 父块过长时的 token 控制 (摘要 / 截取命中子块附近 N 段 / 直接全文)
#      由 retrieval 层根据具体场景决定, chunking 层不做硬截断.
#   4. 父块内容清洗 (压缩多余空行、去除图片占位符等) 也在 retrieval 层完成,
#      chunking 层保留原始内容以保证可逆性和 hash 稳定.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

from forge.core.types import Chunk, ChunkMetadata, ChunkType, Element


def first_page_of(elements: list[Element]) -> int | None:
    """从 element 序列里找第一个非空 page_number, 用于父块定位起始页.

    Element.metadata 应为 ElementMetadata 类型, 但历史 parser 可能误传 dict,
    两种形态都兼容.
    """
    for el in elements:
        meta = getattr(el, "metadata", None)
        if meta is None:
            continue
        # ElementMetadata
        page = getattr(meta, "page_number", None)
        if page is None and isinstance(meta, dict):
            page = meta.get("page_number")
        if isinstance(page, int) and page > 0:
            return page
    return None


@dataclass
class ChunkConfig:
    """切分参数.

    Attributes:
        parent_target_min:        动态选层级时, 父块大小目标下限.
        parent_target_max:        动态选层级时, 父块大小目标上限.
        child_target_chars:       子块目标字符数.
        child_overlap_chars:      子块滑动重叠字符数, 缓解边界断句.
        sliding_parent_chars:     滑窗模式下父块的目标字符数 (软上限,
                                  累加逻辑单元接近此值时输出, 不会切碎单元).
        table_with_context:       表格父块是否合并前置文本作为上下文.
        table_context_max_chars:  表格前置上下文的字符上限 (滑窗模式用).
        skip_toc:                 是否自动识别并跳过目录页.
        skip_code:                是否跳过代码块.
        min_chunk_chars:          过短父块过滤阈值, 小于此值的父块会被丢弃.
        min_titles_required:      层级切分所需的最少标题数, 不足则退化滑窗.
        min_title_density:        标题密度下限 (每千字), 低于则退化滑窗.
        max_title_density:        标题密度上限 (每千字), 高于则退化滑窗.
        too_small_ratio_threshold: 候选层级中"过小父块"占比阈值,
                                  超过则该层级被淘汰.
    """

    # === 父块粒度目标 (软目标, 不是硬上限) ===
    parent_target_min: int = 300
    parent_target_max: int = 2000

    # === 子块滑窗参数 ===
    child_target_chars: int = 400
    child_overlap_chars: int = 80

    # === 滑窗策略专用 ===
    sliding_parent_chars: int = 1500  # 滑窗父块软目标
    table_context_max_chars: int = 300  # 表格前置上下文字符上限

    # === 表格处理 ===
    table_with_context: bool = True

    # === 预处理过滤 ===
    skip_toc: bool = True
    skip_code: bool = True
    min_chunk_chars: int = 30

    # === 动态层级选择的退化阈值 ===
    min_titles_required: int = 3  # 标题数不足则退化滑窗
    min_title_density: float = 0.5  # 每千字标题数下限
    max_title_density: float = 20.0  # 每千字标题数上限
    too_small_ratio_threshold: float = 0.5  # 候选层级被淘汰的"碎块占比"阈值


def compute_text_hash(text: str) -> str:
    """文本 MD5, 用于 chunk_hash."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class BaseChunker(ABC):
    """切分器基类."""

    def __init__(self, config: ChunkConfig | None = None):
        self.config = config or ChunkConfig()

    def chunk(
        self,
        elements: list[Element],
        doc_id: str,
        doc_version: str = "v1",
    ) -> list[Chunk]:
        """主入口: Element → Chunk (父块 + 子块).

        Returns:
            扁平的 Chunk 列表, 顺序为 [P1, C1.1, C1.2, P2, C2.1, ...].
        """
        parents_data = self._build_parents(elements)
        result: list[Chunk] = []

        for idx, parent_data in enumerate(parents_data):
            parent_chunk = self._make_parent_chunk(
                doc_id=doc_id,
                doc_version=doc_version,
                index=idx,
                header_path=parent_data["header_path"],
                content=parent_data["content"],
                source_type=parent_data["source_type"],
                metadata=parent_data.get("metadata") or ChunkMetadata(),
            )
            result.append(parent_chunk)

            children = self._build_children(
                parent=parent_chunk,
                doc_id=doc_id,
                doc_version=doc_version,
            )
            result.extend(children)

        return result

    @abstractmethod
    def _build_parents(self, elements: list[Element]) -> list[dict]:
        """子类实现: 把 Element 列表切分成父块.

        Returns:
            字典列表, 每项至少包含:
                content: str       父块全文
                header_path: str   层级路径
                source_type: str   text / table / mixed
                metadata: dict     附加信息 (可选)
        """

    # ------------------------------------------------------------------
    # 父块生成
    # ------------------------------------------------------------------
    def _make_parent_chunk(
        self,
        doc_id: str,
        doc_version: str,
        index: int,
        header_path: str,
        content: str,
        source_type: str,
        metadata: ChunkMetadata,
    ) -> Chunk:
        """组装父块 Chunk."""
        chunk_id = f"{doc_id}__p_{index:04d}"
        return Chunk(
            chunk_id=chunk_id,
            chunk_type=ChunkType.PARENT,
            content=content,
            source_type=source_type,
            header_path=header_path,
            parent_id=None,
            doc_id=doc_id,
            doc_version=doc_version,
            chunk_hash=compute_text_hash(content),
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # 子块滑窗派生
    # ------------------------------------------------------------------
    def _build_children(
        self,
        parent: Chunk,
        doc_id: str,
        doc_version: str,
    ) -> list[Chunk]:
        """从父块文本按字符数滑窗切子块.

        策略:
            - 父块 ≤ child_target_chars: 整体作为单个子块, 不再切.
            - 否则按 (size = child_target_chars, step = size - overlap) 滑动.
            - 子块的 header_path / source_type 完全继承自父块.
        """
        text = parent.content
        size = self.config.child_target_chars
        overlap = self.config.child_overlap_chars

        if len(text) <= size:
            return [self._make_child(parent, doc_id, doc_version, 0, text)]

        children: list[Chunk] = []
        step = max(1, size - overlap)
        idx = 0
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            sub = text[start:end]
            if sub.strip():
                children.append(self._make_child(parent, doc_id, doc_version, idx, sub))
                idx += 1
            if end >= len(text):
                break
            start += step
        return children

    def _make_child(
        self,
        parent: Chunk,
        doc_id: str,
        doc_version: str,
        index: int,
        content: str,
    ) -> Chunk:
        """组装子块 Chunk, parent_id 指向其父块."""
        chunk_id = f"{parent.chunk_id}__c_{index:04d}"
        return Chunk(
            chunk_id=chunk_id,
            chunk_type=ChunkType.CHILD,
            content=content,
            source_type=parent.source_type,
            header_path=parent.header_path,
            parent_id=parent.chunk_id,
            doc_id=doc_id,
            doc_version=doc_version,
            chunk_hash=compute_text_hash(content),
            metadata=ChunkMetadata(parent_source=parent.source_type),
        )
