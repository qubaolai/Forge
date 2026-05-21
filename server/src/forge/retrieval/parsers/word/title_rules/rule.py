"""标题识别规则的数据结构.

TitleRule 描述 "什么样的文本算标题"; ExcludeRule 描述 "看起来像但不是";
RuleSet 是它们的容器, 由 loader 从 YAML 构建.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# require_bold 的三种取值
RequireBoldMode = Literal["strict", "prefer", "ignore"]


@dataclass
class TitleRule:
    """单条标题识别规则.

    Attributes:
        name: 规则名, 唯一, debug 时定位用.
        pattern: 原始正则字符串.
        compiled: 编译后的正则对象.
        level: 命中后判定的标题层级 (1-6).
        specificity: 规则具体度. 越大越具体, 用于冲突解决的第一序.
                     约定: H1=10, H2=20, H3=30, H4=40, 越深越具体.
        priority: 同 specificity 时的二级排序, 越大越优先.
        require_bold: strict (必须粗体) / prefer (粗体加分) / ignore (不看).
        max_length: 文本最大长度, 超过则不视为标题.
        rule_set: 来源规则集名 (debug 用), 由 loader 填.
    """

    name: str
    pattern: str
    compiled: re.Pattern
    level: int
    specificity: int = 10
    priority: int = 0
    require_bold: RequireBoldMode = "prefer"
    max_length: int = 80
    rule_set: str = ""


@dataclass
class ExcludeRule:
    """黑名单规则.

    命中即认定为正文, 即使后续 TitleRule 也匹配也不当标题.

    Attributes:
        name: 规则名.
        pattern: 原始正则字符串.
        compiled: 编译后的正则.
    """

    name: str
    pattern: str
    compiled: re.Pattern


@dataclass
class RuleSet:
    """规则集.

    由多个 YAML 文件加载合并而来, 是规则引擎的输入.

    Attributes:
        rules: 标题规则列表.
        excludes: 黑名单列表.
        global_max_length: 全局长度上限, 超过此长度的文本一律不当标题.
        forbidden_endings: 标题不允许的结尾字符 (正文标点).
        score_threshold: 格式验证得分阈值, >= 此值才算通过.
        title_size_ratio: 字号高于正文多少倍才算 "字号显著大".
    """

    rules: list[TitleRule] = field(default_factory=list)
    excludes: list[ExcludeRule] = field(default_factory=list)
    global_max_length: int = 80
    forbidden_endings: list[str] = field(default_factory=lambda: ["。", "；", "，", ";"])
    score_threshold: float = 0.8
    title_size_ratio: float = 1.15

    def is_empty(self) -> bool:
        """是否为空规则集."""
        return not self.rules


@dataclass
class RuleHit:
    """规则匹配结果.

    Attributes:
        rule_name: 命中的规则名.
        level: 标题层级.
        score: 格式验证得分.
        matched_text: 实际匹配到的文本片段.
        candidates: 所有命中的规则名 (debug 用).
        score_breakdown: 得分明细 (debug 用).
    """

    rule_name: str
    level: int
    score: float
    matched_text: str = ""
    candidates: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
