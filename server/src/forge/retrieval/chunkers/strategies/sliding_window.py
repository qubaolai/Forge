"""无层级文档的滑动窗口切分器.

适用场景:
    - 纯文本文档 (TXT, 无标题的 MD)
    - HierarchicalChunker 退化路径 (标题分布异常时)

核心改进 (相对原版):
    - 表格原子性保护: 表格 element 永远整块进入某个父块, 绝不被切碎.
    - 表格前置上下文: 表格父块绑定紧邻的前置文本 (字符数受限),
      避免表格裸奔丢失语义.
    - 按"逻辑单元"累加: 不再按字符硬切, 改为累加 element 接近软目标
      时输出, 保证段落 / 表格的完整性.

不变的特性:
    - header_path 兜底为 "(无层级)" / "(段落 N)"
    - 父块之间保留字符级重叠 (从上一个父块末尾取 child_overlap_chars * 2)
"""

from __future__ import annotations

import logging

from forge.core.types import ChunkMetadata, ChunkStrategy, Element, ElementType
from forge.retrieval.chunkers import BaseChunker
from forge.retrieval.chunkers.base import page_range_extra

logger = logging.getLogger(__name__)


class SlidingWindowChunker(BaseChunker):
    """无层级文档的滑动窗口切分器, 表格原子性保护."""

    def _build_parents(self, elements: list[Element]) -> list[dict]:
        """主流程: 构造逻辑单元 → 累加成父块."""
        if not elements:
            return []

        # 1. 把 elements 流转成"逻辑单元"列表
        #    每个单元要么是 (text_elements,) 要么是 (context_elements, table_element)
        units = self._build_logical_units(elements)
        if not units:
            return []

        # 2. 累加单元成父块, 表格单元独立成块, 文本单元按软目标累加
        return self._pack_units_to_parents(units)

    # ==================================================================
    # 阶段 1: 构造逻辑单元
    # ==================================================================
    def _build_logical_units(
        self,
        elements: list[Element],
    ) -> list[dict]:
        """把 element 流切成不可分割的逻辑单元.

        单元类型:
            - text 单元: 单个非表格 element (TEXT/LIST/TITLE/...)
            - table 单元: 表格 element + 紧邻前置文本作为上下文,
                          整体不可分割.

        构造规则:
            - 遇到表格时, 回溯收集紧邻的前置 TEXT/TITLE element
              (累计字符数受 table_context_max_chars 限制)
            - 这些前置 element 从已经构造的 text 单元里"借出",
              避免上下文重复出现在前一个父块和当前表格里
            - 跳过 IMAGE 等无信息元素
        """
        cfg = self.config
        units: list[dict] = []

        for el in elements:
            # 跳过空内容和图片占位
            if not el.content:
                continue
            if el.type == ElementType.IMAGE:
                continue

            if el.type == ElementType.TABLE:
                # 表格: 从已构造的 text 单元里回溯收集上下文
                context_elements = self._extract_table_context(units, cfg.table_context_max_chars)
                units.append(
                    {
                        "type": "table",
                        "elements": [*context_elements, el],
                        "table_element": el,
                        "context_elements": context_elements,
                    }
                )
                continue

            # 普通 element: 单独成一个 text 单元
            units.append(
                {
                    "type": "text",
                    "elements": [el],
                }
            )

        return units

    @staticmethod
    def _extract_table_context(
        units: list[dict],
        max_chars: int,
    ) -> list[Element]:
        """从已构造的 units 末尾回溯, 提取表格的前置上下文.

        策略:
            - 从 units 末尾向前找 type='text' 的单元
            - 累计字符数不超过 max_chars
            - 从末尾截断 (保留离表格最近的内容, 语义最相关)
            - 被借走的 text 单元从 units 中移除, 避免内容重复

        Returns:
            上下文 element 列表 (按原文档顺序).
        """
        if max_chars <= 0:
            return []

        context: list[Element] = []
        accumulated_chars = 0

        # 从末尾向前扫
        while units and units[-1]["type"] == "text":
            unit = units[-1]
            unit_elements = unit["elements"]
            # text 单元目前只含 1 个 element
            el = unit_elements[0]
            el_chars = len(el.content) if el.content else 0

            if accumulated_chars + el_chars > max_chars:
                # 加上这个会超过上限
                if not context:
                    # 一个都没收集到, 截取尾部部分内容作为上下文
                    keep_chars = max_chars
                    truncated_content = el.content[-keep_chars:]
                    truncated_el = Element(
                        type=el.type,
                        content=truncated_content,
                        level=el.level,
                        metadata=el.metadata,
                    )
                    context.insert(0, truncated_el)
                    units.pop()
                break

            context.insert(0, el)
            accumulated_chars += el_chars
            units.pop()

        return context

    # ==================================================================
    # 阶段 2: 单元 → 父块
    # ==================================================================
    def _pack_units_to_parents(self, units: list[dict]) -> list[dict]:
        """把逻辑单元累加成父块.

        策略:
            - 表格单元: 独立成一个父块, 不与其他单元合并 (保证表格原子性
              和表格父块的"可识别性", source_type='table').
            - 文本单元: 累加到接近 sliding_parent_chars 时输出一个父块.
            - 父块之间保留字符级重叠 (从上一个父块末尾取 overlap_chars).
        """
        cfg = self.config
        target = cfg.sliding_parent_chars
        overlap = min(target // 4, cfg.child_overlap_chars * 2)

        parents: list[dict] = []
        text_buffer: list[Element] = []
        text_buffer_chars = 0
        idx = 0
        last_tail = ""  # 上一个父块的尾部, 用于重叠

        def flush_text_buffer() -> None:
            nonlocal text_buffer, text_buffer_chars, idx, last_tail
            if not text_buffer:
                return
            content = self._render_elements(text_buffer)
            if last_tail:
                content = f"{last_tail}\n\n{content}"
            if content.strip():
                extra = page_range_extra(text_buffer)
                parents.append(
                    {
                        "content": content,
                        "header_path": f"(段落 {idx + 1})",
                        "source_type": "text",
                        "elements": list(text_buffer),
                        "metadata": ChunkMetadata(
                            strategy=ChunkStrategy.SLIDING_WINDOW,
                            element_count=len(text_buffer),
                            extra=extra,
                        ),
                    }
                )
                idx += 1
                last_tail = content[-overlap:] if overlap > 0 else ""
            text_buffer = []
            text_buffer_chars = 0

        for unit in units:
            if unit["type"] == "table":
                # 先 flush 当前文本 buffer
                flush_text_buffer()

                # 表格独立成块; 大表按完整行组拆成多个 parent, 避免回灌整表.
                table_parents = self._build_table_parents(unit, idx)
                parents.extend(table_parents)
                idx += len(table_parents)
                # 表格后不接重叠 (表格作为独立单元, 重叠会破坏 markdown)
                last_tail = ""
                continue

            # 文本单元: 累加
            unit_elements = unit["elements"]
            unit_chars = sum(len(e.content) for e in unit_elements if e.content)

            # 如果 buffer 已经接近目标, 加入新单元会超过 → 先 flush
            if text_buffer and text_buffer_chars + unit_chars > target:
                flush_text_buffer()

            text_buffer.extend(unit_elements)
            text_buffer_chars += unit_chars

        flush_text_buffer()
        return parents

    def _build_table_parents(self, unit: dict, idx: int) -> list[dict]:
        """构造表格 parent; 超长表格按完整行组拆分."""
        content = self._render_elements(unit["elements"])
        has_context = bool(unit["context_elements"])
        context_chars = sum(len(e.content) for e in unit["context_elements"])
        base_extra = page_range_extra(unit["elements"])

        def make_parent(
            *,
            content: str,
            elements: list[Element],
            offset: int,
            extra: dict,
        ) -> dict:
            return {
                "content": content,
                "header_path": f"(段落 {idx + offset + 1})",
                "source_type": "table",
                "elements": elements,
                "metadata": ChunkMetadata(
                    strategy=ChunkStrategy.SLIDING_WINDOW,
                    has_context=has_context,
                    context_chars=context_chars,
                    extra=extra,
                ),
            }

        if len(content) <= self.config.parent_target_max:
            return [
                make_parent(
                    content=content,
                    elements=list(unit["elements"]),
                    offset=0,
                    extra=base_extra,
                )
            ]

        table_element = unit["table_element"]
        slices = self._split_table_content(
            content,
            base_extra={"parent_splitter": "table_rows"},
            target_chars=self.config.parent_target_max,
        )
        parents: list[dict] = []
        for offset, table_slice in enumerate(slices):
            extra = dict(base_extra)
            if table_slice.extra:
                extra.update(table_slice.extra)
            split_element = Element(
                type=ElementType.TABLE,
                content=table_slice.content,
                level=table_element.level,
                metadata=table_element.metadata,
            )
            parents.append(
                make_parent(
                    content=table_slice.content,
                    elements=[split_element],
                    offset=offset,
                    extra=extra,
                )
            )
        return parents

    # ==================================================================
    # 渲染
    # ==================================================================
    @staticmethod
    def _render_elements(elements: list[Element]) -> str:
        """把 element 列表渲染成父块全文.

        无层级模式下不再加 # 前缀 (因为没有可信的层级信息),
        TITLE 退化为普通文本输出.
        """
        parts: list[str] = []
        for el in elements:
            if not el.content:
                continue
            if el.type == ElementType.LIST:
                parts.append(f"- {el.content}")
            else:
                parts.append(el.content)
        return "\n\n".join(parts)
