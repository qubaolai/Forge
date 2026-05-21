"""标题规则引擎.

输入文本 + 格式特征, 输出 "是否标题、什么层级、为什么".
流程: 黑名单过滤 → 收集所有候选 → 冲突解决 → 格式打分 → 输出 RuleHit.
"""

from __future__ import annotations

import logging

from ....common.format_profile import FormatProfile
from .rule import RuleHit, RuleSet, TitleRule

logger = logging.getLogger(__name__)


class RuleEngine:
    """规则匹配引擎.

    冲突解决顺序:
        1. specificity 降序 (规则越具体越优先, 比如 1.1.1 优先于 1.1)
        2. 匹配字符长度降序 (兜底的最长匹配)
        3. priority 降序 (人工指定的优先级)

    格式打分 (基础值 1.0):
        - require_bold=strict 且非粗体: 直接 reject (返回 None)
        - 加粗: +0.3, 未加粗 (且 require_bold!=ignore): -0.2
        - 字号显著大于正文: +0.3
        - 末尾在 forbidden_endings: -0.5
        - 长度 < max_length: +0.1; >= max_length 或 > global: reject
        - 阈值 score_threshold 以上才通过.

    Attributes:
        ruleset: 规则集.
        profile: 格式画像, 用于字号判断.
        global_bold_strict_demote: 当文档加粗段落比例过低时,
            是否把 strict 自动降级为 prefer.
        bold_strict_demote_threshold: 触发降级的加粗比例阈值.
    """

    def __init__(
        self,
        ruleset: RuleSet,
        profile: FormatProfile,
        global_bold_strict_demote: bool = True,
        bold_strict_demote_threshold: float = 0.05,
    ):
        """初始化引擎."""
        self.ruleset = ruleset
        self.profile = profile
        self._demote_strict = (
            global_bold_strict_demote and profile.bold_ratio < bold_strict_demote_threshold
        )
        if self._demote_strict:
            logger.info(
                "全文加粗比例 %.2f < %.2f, 本次 strict 规则将降级为 prefer",
                profile.bold_ratio,
                bold_strict_demote_threshold,
            )

    def classify(
        self,
        text: str,
        bold: bool,
        font_size: float | None,
    ) -> RuleHit | None:
        """判断文本是否为标题.

        Args:
            text: 段落文本 (已 strip).
            bold: 段落是否加粗.
            font_size: 段落字号 (pt), 拿不到传 None.

        Returns:
            RuleHit; 不是标题返回 None.
        """
        if not text:
            return None
        # 全局长度上限
        if len(text) > self.ruleset.global_max_length:
            return None
        # 黑名单
        for excl in self.ruleset.excludes:
            if excl.compiled.search(text):
                return None

        # 收集所有候选 (在 _select_best 内部排序)
        candidates: list[tuple[TitleRule, str]] = []
        for rule in self.ruleset.rules:
            m = rule.compiled.match(text)
            if not m:
                continue
            if len(text) > rule.max_length:
                continue
            candidates.append((rule, m.group(0)))

        if not candidates:
            return None

        candidate_names = [r.name for r, _ in candidates]

        # 冲突解决: specificity 降序 → 匹配长度降序 → priority 降序
        candidates.sort(
            key=lambda x: (x[0].specificity, len(x[1]), x[0].priority),
            reverse=True,
        )

        # 依次尝试格式打分, 第一个通过的胜出
        for rule, matched in candidates:
            score, breakdown = self._score(text, bold, font_size, rule)
            if score < 0:
                # 硬约束未通过 (strict 粗体)
                continue
            if score >= self.ruleset.score_threshold:
                return RuleHit(
                    rule_name=rule.name,
                    level=rule.level,
                    score=score,
                    matched_text=matched,
                    candidates=candidate_names,
                    score_breakdown=breakdown,
                )

        return None

    def _score(
        self,
        text: str,
        bold: bool,
        font_size: float | None,
        rule: TitleRule,
    ) -> tuple[float, dict]:
        """格式特征打分.

        Returns:
            (score, breakdown). score=-1 表示硬约束未通过.
        """
        breakdown: dict = {"base": 1.0}
        score = 1.0

        # 粗体硬约束
        require_bold = rule.require_bold
        if require_bold == "strict" and self._demote_strict:
            require_bold = "prefer"

        if require_bold == "strict" and not bold:
            breakdown["bold_strict_reject"] = True
            return -1.0, breakdown

        # 粗体软约束
        if require_bold != "ignore":
            if bold:
                score += 0.3
                breakdown["bold_bonus"] = 0.3
            else:
                score -= 0.2
                breakdown["bold_penalty"] = -0.2

        # 字号
        if self.profile.is_above_body(font_size, self.ruleset.title_size_ratio):
            score += 0.3
            breakdown["font_size_bonus"] = 0.3

        # 长度加分: 短的更像标题
        if len(text) < rule.max_length // 2:
            score += 0.1
            breakdown["short_bonus"] = 0.1

        # 禁止结尾
        if text and text[-1] in self.ruleset.forbidden_endings:
            score -= 0.5
            breakdown["forbidden_ending"] = -0.5

        breakdown["final"] = round(score, 3)
        return score, breakdown
