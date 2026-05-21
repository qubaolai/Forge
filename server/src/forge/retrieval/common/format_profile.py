"""文档格式特征采集.

把字号/粗体的基线统计从 style_detector 抽出来, 作为独立的格式画像.
TitleClassifier 和 HeuristicTitleDetector 都从这里读, 避免重复采样.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# "文档大标题"判定阈值: 标题候选字号中, 出现次数 ≤ 此值的最大字号
# 视为文档级大标题 (例如封面"项目名称"), 不进入 H1-H6 层级体系.
_DOC_TITLE_OCCURRENCE_MAX = 2


@dataclass
class FormatProfile:
    """文档格式画像.

    Attributes:
        body_font_size: 正文字号 (众数), 无法采样时为 None.
        title_size_tiers: 用于 H1-H6 层级映射的标题字号档位, 降序排列.
                          已剔除"文档大标题"字号 (例如只出现 1-2 次的最大字号).
                          tier[0] → H1, tier[1] → H2, 以此类推.
        doc_title_size: 文档大标题的字号; 未识别时为 None.
                        例如封面"XXX 项目说明书"那种独立标题, 不参与层级.
        bold_ratio: 全文中加粗段落的比例 (0.0 - 1.0).
        sample_size: 实际采样的段落数.
    """

    body_font_size: float | None = None
    title_size_tiers: list[float] = field(default_factory=list)
    doc_title_size: float | None = None
    bold_ratio: float = 0.0
    sample_size: int = 0

    @classmethod
    def calibrate(
        cls,
        paragraphs,
        title_size_ratio: float = 1.15,
        max_sample: int = 150,
    ) -> FormatProfile:
        """采样段落构建格式画像.

        Args:
            paragraphs: python-docx 的段落对象列表.
            title_size_ratio: 比正文大多少倍才算标题字号档位.
            max_sample: 最大采样段数.

        Returns:
            构建好的 FormatProfile.
        """
        # 收集每段的 (字号, 是否加粗); 字号缺失记 None
        per_para: list[tuple[float | None, bool]] = []
        non_empty = 0
        bold_count = 0

        for para in paragraphs[:max_sample]:
            text = (para.text or "").strip()
            if not text:
                continue
            non_empty += 1

            size = cls._get_font_size(para)
            bold = cls._is_bold(para)
            per_para.append((size, bold))
            if bold:
                bold_count += 1

        bold_ratio = (bold_count / non_empty) if non_empty else 0.0
        all_sizes = [s for s, _ in per_para if s is not None]

        if not all_sizes:
            logger.warning("无法采样字号, FormatProfile 仅记录粗体比例")
            return cls(
                body_font_size=None,
                title_size_tiers=[],
                doc_title_size=None,
                bold_ratio=bold_ratio,
                sample_size=non_empty,
            )

        # 1. 正文字号 = 全部段落字号的众数
        body_size: float = Counter(all_sizes).most_common(1)[0][0]

        # 2. 标题候选字号: 加粗 + 字号 > 正文 × ratio
        #    用"标题候选"而非"全部大字号"来统计 tier 出现次数, 更准确反映层级
        title_candidate_sizes = [
            s for s, b in per_para if s is not None and b and s > body_size * title_size_ratio
        ]
        # 备选: 整文档都不加粗时, 用"全部大字号"做候选
        if not title_candidate_sizes:
            title_candidate_sizes = [s for s in all_sizes if s > body_size * title_size_ratio]

        # 3. 字号 → 出现次数 计数, 降序排列
        size_counts: list[tuple[float, int]] = sorted(
            Counter(title_candidate_sizes).items(),
            key=lambda x: -x[0],
        )

        # 4. 识别文档大标题:
        #    最大字号若出现次数 ≤ _DOC_TITLE_OCCURRENCE_MAX, 视为文档大标题.
        #    保护: 如果只有一个字号档位, 不剔除 (否则没档位给 H1).
        doc_title_size: float | None = None
        tiers: list[float] = [s for s, _ in size_counts]
        if len(tiers) > 1 and size_counts and size_counts[0][1] <= _DOC_TITLE_OCCURRENCE_MAX:
            doc_title_size = tiers[0]
            tiers = tiers[1:]

        logger.info(
            "FormatProfile: body=%.1fpt, doc_title=%s, tiers=%s, bold_ratio=%.2f, samples=%d",
            body_size,
            f"{doc_title_size}pt" if doc_title_size else "None",
            tiers,
            bold_ratio,
            non_empty,
        )
        return cls(
            body_font_size=body_size,
            title_size_tiers=tiers,
            doc_title_size=doc_title_size,
            bold_ratio=bold_ratio,
            sample_size=non_empty,
        )

    # ---------- 查询接口 ----------

    def is_above_body(self, size: float | None, ratio: float = 1.15) -> bool:
        """字号是否显著大于正文."""
        if size is None or self.body_font_size is None:
            return False
        return size > self.body_font_size * ratio

    def is_doc_title(self, size: float | None, tolerance: float = 0.5) -> bool:
        """字号是否对应文档大标题."""
        if size is None or self.doc_title_size is None:
            return False
        return abs(size - self.doc_title_size) < tolerance

    def get_size_level(self, size: float | None, tolerance: float = 0.5) -> int | None:
        """根据字号返回标题层级 (1-6).

        映射规则:
            - 文档大标题字号 → 1 (顶级)
            - 否则按 title_size_tiers 索引: tier[0] → 1, tier[1] → 2, ...
            - 字号未在已知档位 → None

        Args:
            size: 字号.
            tolerance: 字号匹配容差 (pt).

        Returns:
            标题层级; 未命中返回 None.
        """
        if size is None:
            return None
        if self.is_doc_title(size, tolerance):
            return 1
        for idx, tier in enumerate(self.title_size_tiers):
            if abs(size - tier) < tolerance:
                return min(idx + 1, 6)
        return None

    def get_size_tier_index(self, size: float | None, tolerance: float = 0.5) -> int | None:
        """返回字号在 title_size_tiers 中的索引 (0 = 最大).

        保留此方法用于向后兼容; 新代码应使用 get_size_level().

        Args:
            size: 字号.
            tolerance: 字号匹配容差 (pt).

        Returns:
            档位索引; 未命中返回 None.
        """
        if size is None:
            return None
        for idx, tier in enumerate(self.title_size_tiers):
            if abs(size - tier) < tolerance:
                return idx
        return None

    # ---------- 内部工具 ----------

    @staticmethod
    def _get_font_size(para) -> float | None:
        """取段落首个有字号设置的 run 的字号 (pt)."""
        for run in para.runs:
            if run.font.size is not None:
                return run.font.size.pt
        return None

    @staticmethod
    def _is_bold(para) -> bool:
        """段落所有非空 run 都加粗才算整体加粗."""
        runs = [r for r in para.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)
