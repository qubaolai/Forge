"""标题识别规则引擎包.

对外暴露三个核心类:
    - RuleSetLoader: 从 YAML 加载规则
    - TitleClassifier: 标题判定总入口
    - ClassifyTracer: debug 轨迹收集
"""

from .classifier import ClassifyResult, TitleClassifier
from .debug import ClassifyTracer
from .engine import RuleEngine
from .loader import RuleSetLoader
from .rule import ExcludeRule, RuleHit, RuleSet, TitleRule

__all__ = [
    "TitleClassifier",
    "ClassifyResult",
    "RuleEngine",
    "RuleSetLoader",
    "RuleSet",
    "TitleRule",
    "ExcludeRule",
    "RuleHit",
    "ClassifyTracer",
]
