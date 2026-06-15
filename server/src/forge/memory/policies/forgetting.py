"""事实遗忘策略 (TTL / 衰减 / 容量上限).

两个钩子点 (同一个 Policy 对象, 保证策略一致性):
    is_alive(fact, now)     -> FactStore.recall() 后置过滤, 不返回 "已遗忘" 的
    should_prune(fact, now) -> Celery beat 定时任务调用, True 则物理删除

"软遗忘" 与 "硬删除" 分开:
    is_alive=False 但 should_prune=False 是合法状态 -- 容忍 grace period,
    召回不返回但行还在 DB, 用户在设置页仍能看到/手动救回.

默认装 NoForgetting (永远 alive, 永不 prune), 零行为. 后续视需求
加 TTLForgetting / DecayForgetting / CapacityCapForgetting.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from forge.memory.base import Fact


class ForgettingPolicy(ABC):
    """sync 即可 -- 纯本地计算, 不应触发 IO."""

    @abstractmethod
    def is_alive(self, fact: Fact, now: datetime) -> bool:
        """召回时调用. False 表示 "对当前对话不可见", 但 DB 行可能还在."""
        ...

    @abstractmethod
    def should_prune(self, fact: Fact, now: datetime) -> bool:
        """定时任务调用. True 表示可以物理删除."""
        ...


class NoForgetting(ForgettingPolicy):
    """记忆永久保留, 召回永远可见. 默认."""

    def is_alive(self, fact: Fact, now: datetime) -> bool:
        return True

    def should_prune(self, fact: Fact, now: datetime) -> bool:
        return False
