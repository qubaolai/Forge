"""层级切分策略.

适用场景: Word, Markdown, 任何 Element 流里带 TITLE 的格式.

核心设计:
    A. 健康度检查 (_is_hierarchy_healthy)
       标题数量 / 密度异常时退化到滑窗切分, 避免硬切层级产生碎块.
    B. 动态选父块边界层级 (_analyze_boundary_level)
       基于"以该层级为边界时父块大小分布"评分, 淘汰碎块过多的层级,
       选粒度合适且最深的层级.
    C. header_path 含完整祖先链 (_split_by_heading)
       即使父块边界是 H3, H1/H2 的祖先标题也会出现在 header_path 中.
    D. H 节内容聚合 (_materialize_section)
       一个 H 节内的所有内容 (说明 + 表格 + 图片占位 + JSON) 聚合成
       单个父块. 不做超长二次切分, 父块长度由 retrieval 层处理.
    E. 目录页识别 (_strip_toc)
       识别"开头连续短文本 + 内容与后文 TITLE 高度重复"的目录页, 整段跳过.
    F. 无效块过滤 (_filter_invalid)
       过短或纯占位符的父块丢弃.

不再做的事情:
    - 父块超长二次切分 (原 _split_oversized_section): 移交 retrieval 层
    - 父块字符硬上限: 不再设置 parent_max_chars
"""

from __future__ import annotations

import logging
import statistics

from forge.core.types import ChunkMetadata, ChunkStrategy, Element, ElementType
from forge.retrieval.chunkers import BaseChunker
from forge.retrieval.chunkers.base import first_page_of

logger = logging.getLogger(__name__)

# 这些占位符独占父块时视为无效, 应过滤
_PLACEHOLDER_PATTERNS = frozenset({"[图片]", "​", ""})

# 这些类型不参与父子块生成, 解析时丢弃
_EXCLUDED_TYPES = frozenset(
    {
        ElementType.CODE,
    }
)


class HierarchicalChunker(BaseChunker):
    """层级切分器, 支持动态选边界层级 + H 节聚合 + 异常退化滑窗."""

    # ==================================================================
    # 主流程
    # ==================================================================
    def _build_parents(self, elements: list[Element]) -> list[dict]:
        """主流程: 预处理 → 健康度检查 → 选层级 → 切分组 → 聚合 → 过滤."""
        # 1. 预处理 跳过目录&代码块
        if self.config.skip_toc:
            elements = self._strip_toc(elements)
        if self.config.skip_code:
            elements = self._strip_excluded_types(elements)

        # 2. 健康度检查: 标题分布异常 → 退化滑窗
        if not self._is_hierarchy_healthy(elements):
            return self._delegate_to_sliding_window(elements)

        # 3. 动态选边界层级
        title_levels = [el.level for el in elements if el.type == ElementType.TITLE and el.level]
        boundary_level = self._analyze_boundary_level(elements, title_levels)
        if boundary_level is None:
            # 所有候选都被淘汰 (碎块过多), 退化滑窗
            logger.info("无合适边界层级, 退化滑窗")
            return self._delegate_to_sliding_window(elements)

        logger.info("动态选定父块边界层级: H%d", boundary_level)

        # 4. 按 boundary_level 切 H 节
        sections = self._split_by_heading(elements, boundary_level)

        # 5. 每个 H 节聚合成单父块
        parents: list[dict] = []
        for header_path, section_elements in sections:
            parents.extend(self._materialize_section(header_path, section_elements))

        # 6. 过滤无效父块
        parents = self._filter_invalid(parents)

        logger.info("层级切分完成: %d 父块 (来自 %d 个 H 节)", len(parents), len(sections))
        return parents

    # ==================================================================
    # A. 健康度检查
    # ==================================================================
    def _is_hierarchy_healthy(self, elements: list[Element]) -> bool:
        """判断标题分布是否适合层级切分.

        判定规则 (任一不满足即退化):
            1. 标题总数 ≥ min_titles_required
            2. 标题密度 ∈ [min_title_density, max_title_density] / 千字
        """
        cfg = self.config
        titles = [el for el in elements if el.type == ElementType.TITLE and el.level]

        # 规则 1: 标题数量
        if len(titles) < cfg.min_titles_required:
            logger.info(
                "标题数量不足 (%d < %d), 退化滑窗",
                len(titles),
                cfg.min_titles_required,
            )
            return False

        # 规则 2: 标题密度
        total_chars = sum(
            len(el.content) for el in elements if el.type != ElementType.TITLE and el.content
        )
        if total_chars == 0:
            logger.info("无文本内容, 退化滑窗")
            return False

        density = len(titles) / total_chars * 1000
        if density < cfg.min_title_density:
            logger.info(
                "标题密度过低 (%.2f/千字 < %.2f), 退化滑窗",
                density,
                cfg.min_title_density,
            )
            return False
        if density > cfg.max_title_density:
            logger.info(
                "标题密度过高 (%.2f/千字 > %.2f), 退化滑窗",
                density,
                cfg.max_title_density,
            )
            return False

        return True

    def _delegate_to_sliding_window(self, elements: list[Element]) -> list[dict]:
        """退化到滑窗切分.

        延迟导入避免循环依赖. 复用同一个 config, 由 SlidingWindowChunker
        负责表格原子性保护.
        """
        from .sliding_window import SlidingWindowChunker

        sw = SlidingWindowChunker(self.config)
        return sw._build_parents(elements)

    # ==================================================================
    # B. 动态选边界层级 (基于父块大小分布)
    # ==================================================================
    def _analyze_boundary_level(
        self,
        elements: list[Element],
        title_levels: list[int],
    ) -> int | None:
        """选择最合适的父块边界层级.

        算法:
            1. 对每个候选 level, 模拟切分得到父块大小列表 sizes.
            2. 计算指标:
                 median(sizes)            — 抗异常值的中心趋势
                 too_small_ratio          — sizes 中 < target_min 的比例
            3. 淘汰规则:
                 too_small_ratio > too_small_ratio_threshold  (碎块过多)
            4. 评分规则 (剩余候选):
                 median ∈ [target_min, target_max]: 100 分
                 median 超出区间: 100 - 距离区间边界的字符数 (越接近越高)
            5. 选最深的 (level 数字最大) 且 得分最高的 level.

        Returns:
            选定的 level, 如果所有候选都被淘汰返回 None.
        """
        cfg = self.config
        levels_sorted = sorted(set(title_levels))

        # 模拟切分: 对每个 level 计算 sizes
        candidates: list[tuple[int, float, list[int]]] = []  # (level, score, sizes)
        for lv in levels_sorted:
            sizes = self._simulate_section_sizes(elements, lv)
            if not sizes:
                continue

            too_small = sum(1 for s in sizes if s < cfg.parent_target_min)
            too_small_ratio = too_small / len(sizes)

            # 淘汰: 碎块过多
            if too_small_ratio > cfg.too_small_ratio_threshold:
                logger.debug(
                    "H%d 淘汰: 碎块占比 %.0f%% > %.0f%%",
                    lv,
                    too_small_ratio * 100,
                    cfg.too_small_ratio_threshold * 100,
                )
                continue

            # 评分: 基于 median
            med = statistics.median(sizes)
            if cfg.parent_target_min <= med <= cfg.parent_target_max:
                score = 100.0
            elif med < cfg.parent_target_min:
                score = 100.0 - (cfg.parent_target_min - med)
            else:
                score = 100.0 - (med - cfg.parent_target_max)

            candidates.append((lv, score, sizes))
            logger.debug(
                "H%d 候选: median=%.0f, too_small=%.0f%%, score=%.1f",
                lv,
                med,
                too_small_ratio * 100,
                score,
            )

        if not candidates:
            return None

        # 选最深的、得分最高的 level
        # 排序: score 降序, 同分时 level 降序 (更深)
        candidates.sort(key=lambda x: (-x[1], -x[0]))
        best_lv, best_score, _ = candidates[0]
        logger.debug("最终选 H%d (score=%.1f)", best_lv, best_score)
        return best_lv

    def _simulate_section_sizes(
        self,
        elements: list[Element],
        boundary_level: int,
    ) -> list[int]:
        """模拟以 boundary_level 切分时, 每个父块的字符数.

        遍历 elements, 累计内容字符数, 遇到 level <= boundary_level 的标题
        就 flush 当前累计为一个父块大小. 子标题 (level > boundary) 作为
        内容计入当前父块.
        """
        sizes: list[int] = []
        current_chars = 0

        for el in elements:
            if el.type == ElementType.TITLE and el.level:
                if el.level <= boundary_level:
                    # 边界或祖先标题: flush
                    if current_chars > 0:
                        sizes.append(current_chars)
                    current_chars = 0
                    continue
                # 子标题: 作为内容
                current_chars += len(el.content) if el.content else 0
            elif el.content:
                current_chars += len(el.content)

        if current_chars > 0:
            sizes.append(current_chars)

        return sizes

    # ==================================================================
    # C. 按选定层级切分组 (header_path 含完整祖先链)
    # ==================================================================
    def _split_by_heading(
        self,
        elements: list[Element],
        boundary_level: int,
    ) -> list[tuple[str, list[Element]]]:
        """按 boundary_level 切 H 节.

        关键规则:
            - level < boundary_level (祖先): 更新 header_stack, 触发 flush,
              不进入 section 内容 (它是结构标记).
            - level == boundary_level (边界): flush 旧组 → 开新组,
              header_path = 祖先链 + 当前节标题.
            - level > boundary_level (子标题): 作为内容元素留在当前节内.

        header_stack 按 level 索引存放, 同级标题进入时先 pop 再 push,
        避免兄弟节点变成"父>子"路径.
        """
        sections: list[tuple[str, list[Element]]] = []
        header_stack: list[tuple[int, str]] = []
        current_group: list[Element] = []
        current_path: str = "(文档开头)"

        def flush() -> None:
            if current_group:
                sections.append((current_path, list(current_group)))

        for el in elements:
            if el.type != ElementType.TITLE:
                current_group.append(el)
                continue

            level = el.level or 1

            # 弹出 stack 中所有 level >= 当前的 (兄弟和后辈), 再 push
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()
            header_stack.append((level, el.content))

            if level < boundary_level:
                # 祖先标题: flush 旧组, 更新路径, 不入内容
                flush()
                current_group = []
                current_path = self._build_path(header_stack)
                continue

            if level == boundary_level:
                # 边界标题: 切新组
                flush()
                current_group = []
                current_path = self._build_path(header_stack)
                continue

            # level > boundary_level: 子标题作为内容
            current_group.append(el)

        flush()
        return sections

    @staticmethod
    def _build_path(header_stack: list[tuple[int, str]]) -> str:
        """从 header_stack 构造 header_path."""
        if not header_stack:
            return "(文档开头)"
        return " > ".join(title for _, title in header_stack)

    # ==================================================================
    # D. H 节内容聚合 (单父块, 不做超长切分)
    # ==================================================================
    def _materialize_section(
        self,
        header_path: str,
        elements: list[Element],
    ) -> list[dict]:
        """把一个 H 节落成单个父块字典.

        不再做超长二次切分: 父块长度的控制由 retrieval 层处理.
        节内所有内容 (说明文字 + 表格 + 子标题 + 图片占位 + JSON 等) 合并
        渲染成一个父块, 保证语义完整.
        """
        if not elements:
            return []

        has_table = any(el.type == ElementType.TABLE for el in elements)
        source_type = "mixed" if has_table else "text"

        content = self._render_elements(elements)
        if not content.strip():
            return []

        # 抽取起始页码 (PDF 等格式), 没有时为 None.
        # 父块可能横跨多页, 先记录起始页, 后续可扩展为 page range.
        page = first_page_of(elements)
        extra: dict = {}
        if page is not None:
            extra["page"] = page

        return [
            {
                "content": content,
                "header_path": header_path,
                "source_type": source_type,
                "metadata": ChunkMetadata(
                    strategy=ChunkStrategy.HIERARCHICAL,
                    element_count=len(elements),
                    has_table=has_table,
                    extra=extra,
                ),
            }
        ]

    @staticmethod
    def _render_elements(elements: list[Element]) -> str:
        """把 element 列表渲染成父块全文.

        渲染规则:
            - TITLE (子标题): 加 # 前缀按 markdown 标题输出
            - LIST: 加 - 前缀
            - 其他: 直接输出 content
            - 跳过空内容
        """
        parts: list[str] = []
        for el in elements:
            if not el.content:
                continue
            if el.type == ElementType.TITLE:
                level = el.level or 3
                hashes = "#" * min(level, 6)
                parts.append(f"{hashes} {el.content}")
            elif el.type == ElementType.LIST:
                parts.append(f"- {el.content}")
            else:
                parts.append(el.content)
        return "\n\n".join(parts)

    # ==================================================================
    # E. 目录页识别和过滤
    # ==================================================================
    def _strip_toc(self, elements: list[Element]) -> list[Element]:
        """识别并移除目录页元素.

        识别特征 (3 个全部满足才判定):
            1. 出现在文档开头前 N 个 element 内
            2. 连续多个短 text element (≥5 个, 平均 ≤ 30 字)
            3. 这些 text 内容与后文 TITLE 文本集合的重合率 ≥ 0.8
        """
        if not elements:
            return elements

        title_texts = {
            self._normalize(el.content)
            for el in elements
            if el.type == ElementType.TITLE and el.content
        }
        if not title_texts:
            return elements

        scan_limit = min(50, len(elements))
        segment_start = -1
        segment_end = -1
        for i in range(scan_limit):
            el = elements[i]
            if el.type == ElementType.TEXT and len(el.content.strip()) <= 30:
                if segment_start == -1:
                    segment_start = i
                segment_end = i
            elif el.type == ElementType.TITLE:
                break
            else:
                if segment_start != -1 and segment_end - segment_start + 1 >= 5:
                    break
                segment_start = -1
                segment_end = -1

        if segment_start == -1:
            return elements
        segment_len = segment_end - segment_start + 1
        if segment_len < 5:
            return elements

        segment = elements[segment_start : segment_end + 1]
        match_count = sum(1 for el in segment if self._normalize(el.content) in title_texts)
        ratio = match_count / segment_len
        if ratio < 0.8:
            return elements

        logger.info(
            "识别到目录页: elements[%d:%d] 共 %d 项, 与标题重合 %.0f%%, 已跳过",
            segment_start,
            segment_end + 1,
            segment_len,
            ratio * 100,
        )
        return elements[:segment_start] + elements[segment_end + 1 :]

    def _strip_excluded_types(self, elements: list[Element]) -> list[Element]:
        """过滤掉不参与父子块生成的元素类型 (如 CODE)."""
        if not _EXCLUDED_TYPES:
            return elements
        filtered = [el for el in elements if el.type not in _EXCLUDED_TYPES]
        dropped = len(elements) - len(filtered)
        if dropped:
            logger.info("过滤排除类型 element %d 个", dropped)
        return filtered

    @staticmethod
    def _normalize(text: str) -> str:
        """目录匹配用的归一化: 去空白."""
        return "".join(text.split()) if text else ""

    # ==================================================================
    # F. 无效父块过滤
    # ==================================================================
    def _filter_invalid(self, parents: list[dict]) -> list[dict]:
        """过滤过短或纯占位符的父块."""
        result: list[dict] = []
        dropped = 0
        for p in parents:
            content = p["content"]
            stripped = content.strip()

            if len(stripped) < self.config.min_chunk_chars:
                dropped += 1
                continue

            normalized = stripped.replace("[图片]", "").replace("​", "").strip()
            if not normalized:
                dropped += 1
                continue

            result.append(p)

        if dropped:
            logger.info("过滤无效父块 %d 个 (过短或纯占位符)", dropped)
        return result
