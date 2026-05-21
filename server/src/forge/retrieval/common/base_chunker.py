"""切分器基类.

使用模板方法模式封装父子切分的通用流程. 子类 (WordChunker/ExcelChunker等)
只需实现格式特有的 ``_split_by_parent_boundary`` 方法, 即如何把元素
流切分为父块分组.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator

from forge.core.types import Chunk, ChunkType, Element, ElementType

from .text_utils import content_hash, find_cut_point

logger = logging.getLogger(__name__)


class BaseChunker(ABC):
    """层级切分器基类.

    实现通用的父子切分流程, 包括父块生成、子块切分、重叠控制、
    chunk_id 生成等逻辑. 子类只需重写父块边界识别规则.

    Attributes:
        child_chunk_size: 子块目标字数, 超过此值会触发切分.
        child_chunk_overlap: 子块之间的重叠字数, 避免语义断裂.
        min_chunk_size: 最小 chunk 字数, 小于此值的片段会被丢弃或合并.
    """

    def __init__(
        self,
        child_chunk_size: int = 400,
        child_chunk_overlap: int = 50,
        min_chunk_size: int = 100,
    ):
        """初始化切分器.

        Args:
            child_chunk_size: 子块目标字数, 默认 400.
            child_chunk_overlap: 子块之间的重叠字数, 默认 50.
            min_chunk_size: 最小 chunk 字数阈值, 默认 100.
        """
        self.child_chunk_size = child_chunk_size
        self.child_chunk_overlap = child_chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk(
        self,
        elements: list[Element],
        doc_id: str,
        doc_version: str = "v1",
    ) -> list[Chunk]:
        """将元素列表切分为父子 chunks.

        流程:
            1. 调用 ``_split_by_parent_boundary`` 按格式特有规则分组;
            2. 每组生成一个父块 (PARENT);
            3. 每组内按字数切分出多个子块 (CHILD), 子块指向父块.

        设计取舍: 父块即使内容很短也保留, 因为它代表文档结构上的章节;
        被丢弃的只有完全没有内容的"占位父块" (例如连续的标题中间无任何内容).

        Args:
            elements: Parser 输出的元素列表.
            doc_id: 文档唯一 ID.
            doc_version: 文档版本号.

        Returns:
            父子混合的 Chunk 列表.
        """
        parent_groups = self._split_by_parent_boundary(elements)

        all_chunks: list[Chunk] = []
        for header_path, group_elements in parent_groups:
            if not group_elements:
                continue

            parent_content = self._elements_to_text(group_elements)
            # 关键: 完全为空的父块才跳过, 短父块也保留
            if not parent_content.strip():
                continue

            parent_id = f"{doc_id}__p_{uuid.uuid4().hex[:8]}"
            all_chunks.append(
                Chunk(
                    chunk_id=parent_id,
                    chunk_type=ChunkType.PARENT,
                    content=parent_content,
                    source_type="text",
                    header_path=header_path,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    chunk_hash=content_hash(parent_content),
                )
            )

            # 子块: 仍可以用 min_chunk_size 过滤掉过小的碎片
            for child in self._split_to_children(
                group_elements, parent_id, header_path, doc_id, doc_version
            ):
                # 子块层面才应用最小长度过滤 (但保留表格类子块)
                if child.source_type == "text" and len(child.content) < self.min_chunk_size:
                    continue
                all_chunks.append(child)

        logger.info(
            "doc_id=%s 切分完成: parent=%d, child=%d",
            doc_id,
            sum(1 for c in all_chunks if c.chunk_type == ChunkType.PARENT),
            sum(1 for c in all_chunks if c.chunk_type == ChunkType.CHILD),
        )
        return all_chunks

    @abstractmethod
    def _split_by_parent_boundary(self, elements: list[Element]) -> list[tuple[str, list[Element]]]:
        """按格式特有规则将元素分组为父块.

        Word: 按 H1/H2 标题切分;
        Excel: 按 sheet 切分;
        TXT: 整个文档作为一组.

        Args:
            elements: Parser 输出的元素列表.

        Returns:
            元组列表, 每个元组为 ``(header_path, elements)``.
            header_path 是该分组的层级路径字符串.
        """
        ...

    def _split_to_children(
        self,
        elements: list[Element],
        parent_id: str,
        header_path: str,
        doc_id: str,
        doc_version: str,
    ) -> Iterator[Chunk]:
        """在父块内切分出子块.

        默认实现按字数累积切分, 表格作为独立子块不切断. 子类可覆盖
        以支持其他切分策略 (如按行、按段落).

        Args:
            elements: 父块内的元素列表.
            parent_id: 父块 ID, 子块会指向此 ID.
            header_path: 父块的 header_path, 子块沿用.
            doc_id: 文档 ID.
            doc_version: 文档版本.

        Yields:
            子块 Chunk 对象 (chunk_type 为 CHILD).
        """
        buffer = ""

        for el in elements:
            if el.type == ElementType.TABLE:
                if buffer.strip():
                    yield self._make_child(
                        buffer, parent_id, header_path, doc_id, doc_version, "text"
                    )
                    buffer = ""
                yield self._make_child(
                    el.content, parent_id, header_path, doc_id, doc_version, "table"
                )
                continue

            if el.type == ElementType.IMAGE:
                continue

            text = self._element_prefix(el) + el.content
            buffer = buffer + "\n" + text if buffer else text

            while len(buffer) >= self.child_chunk_size:
                cut = find_cut_point(buffer, self.child_chunk_size)
                chunk_text = buffer[:cut].strip()
                if chunk_text:
                    yield self._make_child(
                        chunk_text, parent_id, header_path, doc_id, doc_version, "text"
                    )
                overlap_start = max(0, cut - self.child_chunk_overlap)
                buffer = buffer[overlap_start:]

        if buffer.strip() and len(buffer) >= self.min_chunk_size:
            yield self._make_child(buffer, parent_id, header_path, doc_id, doc_version, "text")

    @staticmethod
    def _element_prefix(el: Element) -> str:
        """为元素生成 Markdown 格式前缀.

        帮助下游 LLM 理解 chunk 内的结构层级. 例如 TITLE 转为 ``## ``,
        LIST 转为 ``- ``.

        Args:
            el: 待处理的元素.

        Returns:
            前缀字符串, 普通文本返回空字符串.
        """
        if el.type == ElementType.TITLE:
            return "#" * (el.level or 3) + " "
        if el.type == ElementType.LIST:
            return "- "
        return ""

    @classmethod
    def _elements_to_text(cls, elements: list[Element]) -> str:
        """将元素列表拼接为带结构前缀的文本.

        Args:
            elements: 待拼接的元素列表.

        Returns:
            以双换行分隔的完整文本.
        """
        parts = [cls._element_prefix(el) + el.content for el in elements]
        return "\n\n".join(parts)

    def _make_child(
        self,
        content: str,
        parent_id: str,
        header_path: str,
        doc_id: str,
        doc_version: str,
        source_type: str,
    ) -> Chunk:
        """构造子块 Chunk 对象.

        Args:
            content: 子块文本内容.
            parent_id: 所属父块 ID.
            header_path: 层级路径.
            doc_id: 文档 ID.
            doc_version: 文档版本.
            source_type: 内容来源类型 (text/table 等).

        Returns:
            填充完毕的 Chunk 对象.
        """
        content = content.strip()
        return Chunk(
            chunk_id=f"{doc_id}__c_{uuid.uuid4().hex[:8]}",
            chunk_type=ChunkType.CHILD,
            content=content,
            source_type=source_type,
            header_path=header_path,
            parent_id=parent_id,
            doc_id=doc_id,
            doc_version=doc_version,
            chunk_hash=content_hash(content),
        )
