"""记忆策略层: 写入冲突 + 遗忘.

每类策略 = 一个 ABC + 至少一个 NoOp 实现.
Stage 2 全用 NoOp, Stage 3+ 视需求加真实现, 调用方零改动.
"""

from .conflict import (
    ConflictResolver,
    Insert,
    Merge,
    Replace,
    Resolution,
    Skip,
)
from .forgetting import ForgettingPolicy, NoForgetting

__all__ = [
    "ConflictResolver",
    "Resolution",
    "Insert",
    "Replace",
    "Merge",
    "Skip",
    "ForgettingPolicy",
    "NoForgetting",
]
