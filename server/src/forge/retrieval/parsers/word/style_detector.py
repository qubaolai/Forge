"""无样式 Word 文档的标题启发式识别 (最终兜底).

新架构下, 此模块作为 TitleClassifier 的第三阶段兜底:
当原生 Title 和规则引擎都未命中时, 用 "字号 + 粗体" 启发式做最后判断.
原本的 calibrate 已抽到 FormatProfile, 此处只保留判定逻辑.
"""

from __future__ import annotations

import logging

from docx.text.paragraph import Paragraph

from ...common.format_profile import FormatProfile

logger = logging.getLogger(__name__)


class HeuristicTitleDetector:
    """基于字号和字重的标题启发式识别器 (兜底用).

    新流程下不再做正则识别 (那部分已交给 RuleEngine), 只看字号和粗体.

    Attributes:
        profile: 文档格式画像, 由 FormatProfile.calibrate 产出.
        title_font_size_ratio: 判定字号显著大于正文的比例阈值.
        title_max_length: 标题最大长度.
    """

    def __init__(
        self,
        profile: FormatProfile,
        title_font_size_ratio: float = 1.15,
        title_max_length: int = 80,
    ):
        """初始化启发式识别器.

        Args:
            profile: 已 calibrate 完毕的格式画像.
            title_font_size_ratio: 字号比例阈值, 默认 1.15.
            title_max_length: 标题最大长度, 默认 80 字符.
        """
        self.profile = profile
        self.title_font_size_ratio = title_font_size_ratio
        self.title_max_length = title_max_length

    def detect_title_level(self, para: Paragraph) -> int | None:
        """启发式判断段落是否为标题及其层级.

        判断逻辑:
            1. Word Heading 样式 → 直接读层级 (规则引擎不一定接触样式信息,
               这里仍做一次以防遗漏);
            2. 字号显著大于正文 + 加粗 → 按字号档位定级.

        Args:
            para: 待判断段落.

        Returns:
            标题层级 (1-6); 非标题返回 None.
        """
        text = para.text.strip()
        if not text or len(text) > self.title_max_length:
            return None

        # 1. Word Heading 样式
        style_name = ""
        if para.style is not None and para.style.name is not None:
            style_name = para.style.name.lower()

        if "heading" in style_name or "标题" in style_name:
            for ch in style_name:
                if ch.isdigit():
                    return min(int(ch), 6)
            return 1

        # 2. 字号 + 加粗
        if self.profile.body_font_size is None:
            return None
        size = self._get_font_size(para)
        if not self.profile.is_above_body(size, self.title_font_size_ratio):
            return None
        if not self._is_bold(para):
            return None

        # 用 FormatProfile.get_size_level 做层级映射:
        #   - 文档大标题字号 (出现 ≤ 2 次的最大字号) → H1
        #   - 其他档位按 tier 索引 +1 映射到 H1-H6
        level = self.profile.get_size_level(size)
        if level is None:
            return 2  # 字号比正文大但不在已知档位, 默认 H2
        return level

    @staticmethod
    def _get_font_size(para: Paragraph) -> float | None:
        """取段落首个有字号设置的 run 的字号 (pt)."""
        for run in para.runs:
            if run.font.size is not None:
                return run.font.size.pt
        return None

    @staticmethod
    def _is_bold(para: Paragraph) -> bool:
        """段落所有非空 run 都加粗才算整体加粗."""
        runs = [r for r in para.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)
