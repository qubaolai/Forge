"""记忆作用域: 隔离的最小单位 (值对象, 不是策略).

为什么是值对象不是 IsolationStrategy:
    "要不要隔离" 永远只有一种答案 (要), 变化点只在 "隔离边界是什么".
    当前 (且可预期的将来) 唯一隔离维度是 user —— server 端不做
    workspace / 多租户, 若未来需要, 给本值对象添字段 + Store 查询加过滤即可,
    不存在 "换一个隔离策略实现" 这件事.

所有 Store API (SummaryStore / FactStore) 强制吃 MemoryScope, 不再单独传
user_id -- 在签名层就阻止 "忘了过滤" 这种 bug.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScope:
    """记忆的所有权 / 可见性边界.

    Attributes:
        user_id: 用户 ID, 唯一的隔离维度.
    """

    user_id: str

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("MemoryScope.user_id 不能为空")

    @classmethod
    def for_user(cls, user_id: str) -> MemoryScope:
        """按用户隔离的标准构造方式."""
        return cls(user_id=user_id)
