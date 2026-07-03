"""切分层基类.

职责分工:
    - 子类实现 _build_parents (Element → 父块字典列表)
    - 基类统一处理:
        * 父块/子块的 chunk_id 生成
        * 父块全文 hash 计算
        * 子块结构感知派生 (表格按行组, 文本按段落/软边界)
        * parent_id 绑定

子块派生说明:
    父块负责保证回灌给 LLM 的上下文完整, 子块负责向量/BM25 召回精度.
    因此 child 不能再统一字符硬切: 表格按完整行组切, 文本优先按段落
    打包, 只有单段过长时才退化为带软边界的滑窗.

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
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from forge.core.types import Chunk, ChunkMetadata, ChunkType, Element, ElementType


_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class _ChildSlice:
    """子块内容片段, 用于在生成 Chunk 前携带拆分来源元数据."""

    content: str
    source_type: str | None = None
    extra: dict | None = None


def _page_number_of(el: Element) -> int | None:
    meta = getattr(el, "metadata", None)
    if meta is None:
        return None
    page = getattr(meta, "page_number", None)
    if page is None and isinstance(meta, dict):
        page = meta.get("page_number")
    if isinstance(page, int) and page > 0:
        return page
    return None


def first_page_of(elements: list[Element]) -> int | None:
    """从 element 序列里找第一个非空 page_number, 用于父块定位起始页."""
    for el in elements:
        page = _page_number_of(el)
        if page is not None:
            return page
    return None


def page_range_extra(elements: list[Element]) -> dict:
    """从 Element 元数据抽取页码范围, 保留 page 兼容旧调用方."""
    pages = [page for el in elements if (page := _page_number_of(el)) is not None]
    if not pages:
        return {}
    page_start = min(pages)
    page_end = max(pages)
    return {
        "page": first_page_of(elements) or page_start,
        "page_start": page_start,
        "page_end": page_end,
    }


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

    # === 表格子块 (Excel + MD/Word 共用) ===
    # 一张表 (sheet / md 表格) 优先整体作为一个子块 (保表结构定义语义),
    # 只有整表 + 上下文超过此字符预算时, 才按完整行组拆分.
    table_child_max_chars: int = 2_500

    # === Excel 父块粒度 (整 sheet 一块 vs 拆行组) ===
    excel_small_sheet_max_rows: int = 200  # <= 此行数标记为 small_sheet (仅影响 mode 标签)
    excel_sheet_parent_max_rows: int = 2_000  # <= 此行数整 sheet 作单父块, 超过拆行组
    excel_sheet_parent_max_chars: int = 200_000  # 整 sheet 单父块字符上限
    excel_large_parent_rows: int = 300  # 大表拆分时每个父块的行数
    excel_large_parent_max_chars: int = 200_000  # 大表拆分时每个父块字符上限

    # === 滑窗策略专用 ===
    sliding_parent_chars: int = 1500  # 滑窗父块软目标
    table_context_max_chars: int = 500  # 表格前置上下文字符上限 (含紧邻上文段落)

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
                elements=parent_data.get("elements"),
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
                elements: list     父块包含的原始 Element 序列 (可选)
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
    # 子块结构感知派生
    # ------------------------------------------------------------------
    def _build_children(
        self,
        parent: Chunk,
        doc_id: str,
        doc_version: str,
        elements: list[Element] | None = None,
    ) -> list[Chunk]:
        """从父块派生召回用子块.

        策略:
            - 优先使用 parent_data.elements, 避免从渲染字符串反推结构.
            - table: 按 Markdown 表格完整行组切, 每个子块重复表头.
            - mixed: 先识别 Markdown 表格段, 表格和文本分别切.
            - text: 按段落打包; 单段过长时才按软边界滑窗.
        """
        text = parent.content
        if not text.strip():
            return []

        if elements:
            slices = self._split_element_children(parent, elements)
        elif parent.source_type == "table":
            slices = self._split_table_content(text)
        elif parent.source_type == "mixed":
            slices = self._split_mixed_content(text)
        else:
            slices = self._split_text_content(text)

        children: list[Chunk] = []
        for idx, child_slice in enumerate(s for s in slices if s.content.strip()):
            children.append(
                self._make_child(
                    parent,
                    doc_id,
                    doc_version,
                    idx,
                    child_slice.content,
                    source_type=child_slice.source_type,
                    extra=child_slice.extra,
                )
            )
        return children

    def _split_element_children(
        self,
        parent: Chunk,
        elements: list[Element],
    ) -> list[_ChildSlice]:
        """基于原始 Element 派生 child, 保留 parser 已识别出的结构边界."""
        result: list[_ChildSlice] = []
        text_buffer: list[str] = []
        table_index = 0

        def flush_text() -> None:
            nonlocal text_buffer
            if not text_buffer:
                return
            result.extend(self._split_text_content("\n\n".join(text_buffer)))
            text_buffer = []

        for el in elements:
            if not el.content:
                continue
            if el.type == ElementType.IMAGE:
                continue
            if el.type == ElementType.TABLE:
                context = self._table_context_from_text_buffer(text_buffer)
                if parent.source_type == "table":
                    text_buffer = []
                else:
                    flush_text()
                table_text = self._render_table_child(context, [el.content], [], "")
                result.extend(
                    self._split_table_content(
                        table_text,
                        base_extra={"table_index": table_index},
                        target_chars=self.config.table_child_max_chars,
                    )
                )
                table_index += 1
                continue
            if el.type == ElementType.CODE:
                flush_text()
                result.extend(
                    self._split_text_content(
                        el.content,
                        source_type="code",
                        base_extra={"element_type": ElementType.CODE.value},
                    )
                )
                continue

            text_buffer.append(self._render_element_for_child(el))

        flush_text()
        return result or self._split_text_content(parent.content)

    def _split_mixed_content(self, text: str) -> list[_ChildSlice]:
        """拆 mixed 父块: Markdown 表格段和普通文本段分别处理."""
        if len(text) <= self.config.child_target_chars:
            return [
                _ChildSlice(
                    text,
                    extra={"splitter": "whole"},
                )
            ]

        segments = self._split_markdown_table_segments(text)
        if len(segments) == 1 and segments[0][0] == "text":
            return self._split_text_content(text)

        result: list[_ChildSlice] = []
        for segment_type, segment_content in segments:
            if not segment_content.strip():
                continue
            if segment_type == "table":
                result.extend(
                    self._split_table_content(
                        segment_content,
                        target_chars=self.config.table_child_max_chars,
                    )
                )
            else:
                result.extend(self._split_text_content(segment_content))
        return result

    def _split_text_content(
        self,
        text: str,
        *,
        source_type: str = "text",
        base_extra: dict | None = None,
    ) -> list[_ChildSlice]:
        """文本 child: 优先按段落边界打包, 长段才退化软滑窗."""
        text = text.strip()
        size = self.config.child_target_chars
        if len(text) <= size:
            return [
                _ChildSlice(
                    text,
                    source_type=source_type,
                    extra=self._merge_extra(base_extra, {"splitter": "whole"}),
                )
            ]

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if not paragraphs:
            return []

        result: list[_ChildSlice] = []
        buffer: list[str] = []

        def flush_buffer() -> None:
            nonlocal buffer
            if not buffer:
                return
            result.append(
                _ChildSlice(
                    "\n\n".join(buffer),
                    source_type=source_type,
                    extra=self._merge_extra(base_extra, {"splitter": "paragraph"}),
                )
            )
            buffer = []

        for paragraph in paragraphs:
            if len(paragraph) > size:
                flush_buffer()
                result.extend(
                    self._split_long_text(
                        paragraph,
                        source_type=source_type,
                        base_extra=base_extra,
                    )
                )
                continue

            candidate = "\n\n".join([*buffer, paragraph]) if buffer else paragraph
            if buffer and len(candidate) > size:
                flush_buffer()
                buffer = [paragraph]
            else:
                buffer.append(paragraph)

        flush_buffer()
        return result

    def _split_table_content(
        self,
        text: str,
        *,
        base_extra: dict | None = None,
        target_chars: int | None = None,
        max_rows: int | None = None,
        splitter_label: str = "table_rows",
    ) -> list[_ChildSlice]:
        """表格 child: 保留完整表头和完整数据行, 不按字符切断行.

        Args:
            target_chars: 单个行组的字符软上限; None 用 child_target_chars.
            max_rows:     单个行组的行数硬上限; None 表示不限行数 (仅按字符).
            splitter_label: 写入子块 extra.splitter 的标签, 便于区分来源
                            (普通表格 table_rows / Excel excel_table_rows).
        """
        size = target_chars or self.config.child_target_chars
        lines = text.strip().splitlines()
        table_start = self._find_table_start(lines)
        if table_start is None:
            return self._split_text_content(text, base_extra=base_extra)

        table_end = table_start
        while table_end < len(lines) and self._is_table_line(lines[table_end]):
            table_end += 1

        context = "\n".join(lines[:table_start]).strip()
        table_lines = lines[table_start:table_end]
        suffix = "\n".join(lines[table_end:]).strip()
        if not table_lines:
            return self._split_text_content(text)

        header_len = 2 if len(table_lines) >= 2 and self._is_table_separator(table_lines[1]) else 1
        header_lines = table_lines[:header_len]
        data_rows = table_lines[header_len:]
        if not data_rows:
            content = self._render_table_child(context, header_lines, [], suffix)
            return [
                _ChildSlice(
                    content,
                    source_type="table",
                    extra=self._merge_extra(
                        base_extra,
                        {"splitter": "table_whole", "row_count": 0},
                    ),
                )
            ]

        result: list[_ChildSlice] = []
        row_buffer: list[str] = []
        row_start = 1

        def flush_rows(*, include_suffix: bool = False) -> None:
            nonlocal row_buffer, row_start
            if not row_buffer:
                return
            row_end = row_start + len(row_buffer) - 1
            content = self._render_table_child(
                context,
                header_lines,
                row_buffer,
                suffix if include_suffix else "",
            )
            result.append(
                _ChildSlice(
                    content,
                    source_type="table",
                    extra=self._merge_extra(
                        base_extra,
                        {
                            "splitter": splitter_label,
                            "row_start": row_start,
                            "row_end": row_end,
                            "row_count": len(row_buffer),
                        },
                    ),
                )
            )
            row_buffer = []
            row_start = row_end + 1

        for row_index, row in enumerate(data_rows, start=1):
            # 行数硬上限 (Excel 用): 先按行数 flush, 再走字符软上限判断
            if max_rows is not None and row_buffer and len(row_buffer) >= max_rows:
                flush_rows()
                row_start = row_index
            candidate_rows = [*row_buffer, row]
            candidate = self._render_table_child(context, header_lines, candidate_rows, "")
            if row_buffer and len(candidate) > size:
                flush_rows()
                row_start = row_index
                row_buffer = [row]
            else:
                row_buffer.append(row)

        flush_rows(include_suffix=True)
        return result

    def _split_long_text(
        self,
        text: str,
        *,
        source_type: str,
        base_extra: dict | None = None,
    ) -> list[_ChildSlice]:
        """长段兜底: 按软边界滑窗, 优先在换行/句末标点处切."""
        size = self.config.child_target_chars
        overlap = self.config.child_overlap_chars
        step = max(1, size - overlap)
        result: list[_ChildSlice] = []
        start = 0

        while start < len(text):
            hard_end = min(start + size, len(text))
            end = hard_end if hard_end >= len(text) else self._find_soft_cut(text, start, hard_end)
            fragment = text[start:end].strip()
            if fragment:
                result.append(
                    _ChildSlice(
                        fragment,
                        source_type=source_type,
                        extra=self._merge_extra(base_extra, {"splitter": "soft_window"}),
                    )
                )
            if end >= len(text):
                break
            next_start = max(0, end - overlap)
            if next_start <= start:
                next_start = start + step
            start = next_start

        return result

    @staticmethod
    def _render_table_child(
        context: str,
        header_lines: list[str],
        row_lines: list[str],
        suffix: str,
    ) -> str:
        parts: list[str] = []
        if context:
            parts.append(context)
        if header_lines:
            parts.append("\n".join(header_lines))
        if row_lines:
            parts.append("\n".join(row_lines))
        if suffix:
            parts.append(suffix)
        return "\n\n".join(parts).strip()

    def _table_context_from_text_buffer(self, text_buffer: list[str]) -> str:
        if not text_buffer:
            return ""
        max_chars = self.config.table_context_max_chars
        if max_chars <= 0:
            return ""
        context = "\n\n".join(text_buffer).strip()
        if len(context) <= max_chars:
            return context
        return context[-max_chars:].strip()

    @staticmethod
    def _render_element_for_child(el: Element) -> str:
        if el.type == ElementType.TITLE:
            level = el.level or 3
            return f"{'#' * min(level, 6)} {el.content}"
        if el.type == ElementType.LIST:
            return f"- {el.content}"
        return el.content

    @staticmethod
    def _merge_extra(base: dict | None, extra: dict) -> dict:
        merged = dict(base or {})
        merged.update(extra)
        return merged

    @staticmethod
    def _find_soft_cut(text: str, start: int, hard_end: int) -> int:
        """从 hard_end 向前找较自然的截断点, 找不到则硬切."""
        lower = start + max(1, int((hard_end - start) * 0.6))
        for pos in range(hard_end - 1, lower - 1, -1):
            if text[pos] in "\n。！？；.!?;":
                return pos + 1
        return hard_end

    @classmethod
    def _split_markdown_table_segments(cls, text: str) -> list[tuple[str, str]]:
        """把文本拆成 text/table 片段, table 片段为连续 Markdown 表格行."""
        lines = text.splitlines()
        segments: list[tuple[str, str]] = []
        buffer: list[str] = []
        i = 0

        def flush_text() -> None:
            nonlocal buffer
            content = "\n".join(buffer).strip()
            if content:
                segments.append(("text", content))
            buffer = []

        while i < len(lines):
            if cls._is_table_start(lines, i):
                flush_text()
                table_lines: list[str] = []
                while i < len(lines) and cls._is_table_line(lines[i]):
                    table_lines.append(lines[i])
                    i += 1
                segments.append(("table", "\n".join(table_lines)))
                continue
            buffer.append(lines[i])
            i += 1

        flush_text()
        return segments

    @classmethod
    def _find_table_start(cls, lines: list[str]) -> int | None:
        for i in range(len(lines)):
            if cls._is_table_start(lines, i):
                return i
        return None

    @classmethod
    def _is_table_start(cls, lines: list[str], index: int) -> bool:
        return (
            index + 1 < len(lines)
            and cls._is_table_line(lines[index])
            and cls._is_table_separator(lines[index + 1])
        )

    @staticmethod
    def _is_table_line(line: str) -> bool:
        stripped = line.strip()
        return "|" in stripped and bool(stripped.strip("|").strip())

    @staticmethod
    def _is_table_separator(line: str) -> bool:
        stripped = line.strip().strip("|").strip()
        if not stripped:
            return False
        cells = [cell.strip().replace(" ", "") for cell in stripped.split("|")]
        return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells)

    def _make_child(
        self,
        parent: Chunk,
        doc_id: str,
        doc_version: str,
        index: int,
        content: str,
        *,
        source_type: str | None = None,
        extra: dict | None = None,
    ) -> Chunk:
        """组装子块 Chunk, parent_id 指向其父块."""
        chunk_id = f"{parent.chunk_id}__c_{index:04d}"
        metadata = ChunkMetadata(parent_source=parent.source_type)
        if extra:
            metadata.extra.update(extra)
        return Chunk(
            chunk_id=chunk_id,
            chunk_type=ChunkType.CHILD,
            content=content.strip(),
            source_type=source_type or parent.source_type,
            header_path=parent.header_path,
            parent_id=parent.chunk_id,
            doc_id=doc_id,
            doc_version=doc_version,
            chunk_hash=compute_text_hash(content.strip()),
            metadata=metadata,
        )
