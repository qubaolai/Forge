"""标题分类器 - 三阶段总入口.

判定优先级:
    1. 原生 Title (original_label="Title") → 直接信任, 不走规则
    2. RuleEngine 规则匹配 → 命中即认定
    3. HeuristicTitleDetector 字号 + 粗体兜底 → 仅当规则全 miss
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .engine import RuleEngine

logger = logging.getLogger(__name__)


@dataclass
class ClassifyResult:
    """分类结果.

    Attributes:
        is_title: 是否为标题.
        level: 标题层级 (1-6); 非标题为 None.
        source: 判定来源:
                  native_style / rule:<n> / heuristic / fallback
        rule_name: 命中的规则名 (source=rule:* 时填充).
        score: 格式验证得分 (走规则时填充).
        candidates: 所有命中的规则名 (debug 用).
        score_breakdown: 得分明细 (debug 用).
    """

    is_title: bool
    level: int | None = None
    source: str = "fallback"
    rule_name: str | None = None
    score: float | None = None
    candidates: list[str] | None = None
    score_breakdown: dict | None = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []
        if self.score_breakdown is None:
            self.score_breakdown = {}


class TitleClassifier:
    """三阶段标题分类器.

    Attributes:
        engine: 规则引擎.
        fallback: 启发式兜底, 可为 None (无兜底).
    """

    def __init__(
        self,
        engine: RuleEngine | None = None,
        fallback=None,  # HeuristicTitleDetector | None, 用 Any 避免循环依赖
    ):
        """初始化分类器."""
        self.engine = engine
        self.fallback = fallback

    def classify(
        self,
        text: str,
        bold: bool,
        font_size: float | None,
        original_label: str | None = None,
        native_level: int | None = None,
        para=None,
    ) -> ClassifyResult:
        """对一段文本做三阶段判定.

        Args:
            text: 段落文本 (已 strip).
            bold: 是否加粗.
            font_size: 字号 (pt).
            original_label: 解析器给的原始类别. "Title" 时直接信任.
            native_level: 调用方已知的原生标题层级 (1-6).
                          适用场景:
                            - python-docx 路径: 从 Heading N 样式名读出层级;
                            - Unstructured 路径: 从 metadata.category_depth 读出层级.
                          仅在 original_label="Title" 时生效, 为 None 时回退默认值 1.
            para: python-docx Paragraph 对象, 仅 fallback 阶段需要.
                  Unstructured 路径无此对象时传 None,
                  此时 fallback 阶段被跳过.

        Returns:
            ClassifyResult.
        """
        # 阶段 1: 原生 Title 直接信任
        if original_label == "Title":
            level = (
                native_level
                if native_level is not None
                else self._level_from_native(original_label, text)
            )
            return ClassifyResult(
                is_title=True,
                level=level,
                source="native_style",
            )

        # 阶段 2: 规则引擎
        if self.engine is not None:
            hit = self.engine.classify(text, bold, font_size)
            if hit is not None:
                return ClassifyResult(
                    is_title=True,
                    level=hit.level,
                    source=f"rule:{hit.rule_name}",
                    rule_name=hit.rule_name,
                    score=hit.score,
                    candidates=hit.candidates,
                    score_breakdown=hit.score_breakdown,
                )

        # 阶段 3: 启发式兜底 (仅 python-docx 路径)
        if self.fallback is not None and para is not None:
            level = self.fallback.detect_title_level(para)
            if level is not None:
                return ClassifyResult(
                    is_title=True,
                    level=level,
                    source="heuristic",
                )

        return ClassifyResult(is_title=False, source="fallback")

    @staticmethod
    def _level_from_native(label: str, text: str) -> int:
        """原生 Title 的层级兜底推断.

        当调用方未通过 native_level 提供层级时, 用此方法返回保守默认值.
        正常情况调用方都会传 native_level (python-docx 读 Heading N 样式名,
        Unstructured 读 metadata.category_depth), 这里只是最后一道防线.

        Args:
            label: 原始 label.
            text: 标题文本.

        Returns:
            默认层级 1.
        """
        return 1
