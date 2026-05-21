"""LoopGuard 框架: 给 ReAct 循环加 "软引导 / 强制收尾" 多层兜底.

每个 Guard 关注一种情况:
    - StepSafetyNet     : 步数接近上限 -> 引导收尾, 最后一步 force_text_only
    - StuckDetector     : 连续相同 tool 调用 -> 强引导, 5 次 force_stop
    - TokenBudgetGuard  : 累计 token 接近预算 -> 提示 / force_stop
    - WallClockGuard    : 墙钟超时 -> 提示 / force_stop

多个 Guard 同时生效, Runner 按顺序问每个; 任一返回 force_stop 就强制纯文本.

业务流程不依赖 LoopGuard, 它是兜底机制; 每个 turn 创建新实例 (避免状态泄漏).
"""

from .base import Guidance, LoopGuard, LoopState
from .step_safety_net import StepSafetyNet
from .stuck_detector import StuckDetector
from .token_budget import TokenBudgetGuard
from .wall_clock import WallClockGuard

__all__ = [
    "LoopGuard",
    "LoopState",
    "Guidance",
    "StepSafetyNet",
    "StuckDetector",
    "TokenBudgetGuard",
    "WallClockGuard",
]
