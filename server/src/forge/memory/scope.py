"""记忆作用域: 隔离的最小单位 (值对象, 不是策略).

为什么是值对象不是 IsolationStrategy:
    "要不要隔离" 永远只有一种答案 (要), 变化点只在 "隔离边界是什么".
    Stage 2 只有 user 级; Stage 3+ 加 workspace / tenant 时, 只是给值对象
    添字段, Store 查询里多一个过滤项, 不存在 "换一个隔离策略实现" 这件事.

所有 Store API (SummaryStore / FactStore) 强制吃 MemoryScope, 不再单独传
user_id -- 在签名层就阻止 "忘了过滤" 这种 bug.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScope:
    """记忆的所有权 / 可见性边界.

    Attributes:
        user_id: 用户 ID, Stage 2 唯一的隔离维度.
        workspace_id: 工作区 ID, Stage 3+ 用. None = 不属于任何工作区.
        tenant_id: 租户 ID, 多租户部署用. None = 单租户.
    """

    user_id: str
    workspace_id: str | None = None
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("MemoryScope.user_id 不能为空")

    @classmethod
    def for_user(cls, user_id: str) -> MemoryScope:
        """最常用的构造方式: 仅按用户隔离."""
        return cls(user_id=user_id)
