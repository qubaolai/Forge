"""A/B 测试路由: 按用户 hash 稳定分桶, 对比不同模型 / 同模型不同参数.

设计:
    - 用户级稳定: 同一 user_id 永远落到同一组 (基于 hash, 不依赖外部存储)
    - preferred_provider/model 存在时跳过 (用户 pin 优先)
    - 多组按权重分配, weights 之和不必为 100 (会归一化)
    - 命中的组可以指定一个固定 (provider, model), 也可以不指定 (回归默认顺序)

典型用例:
    1. 评测新模型: 50% claude-sonnet-4-6 (A), 50% gpt-4o (B)
    2. 灰度发布: 95% 旧模型 (control), 5% 新模型 (treatment)

数据收集:
    决策 reason 字段含 "abtest:{group_name}", 审计日志可据此聚合各组指标.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from .base import Candidate, Router, RoutingDecision, RoutingRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ABTestGroup:
    """一个 A/B 测试组."""

    name: str
    """组标识 (control / treatment_a / treatment_b ...)"""

    weight: float
    """分配权重. 各组归一化后决定流量比例."""

    provider: str | None = None
    """该组使用的 provider. None 表示沿用 available 默认顺序."""

    model: str | None = None


@dataclass
class ABTestRouter(Router):
    """A/B 测试路由.

    决策流程:
        1. preferred_provider/model 存在 → 跳过 (不污染用户体验)
        2. user_id 为空 → 跳过 (匿名用户不参与实验)
        3. 按 sha256(experiment_name + ":" + user_id) % total_weight 选组
        4. 组指定了 (provider, model) → 返回; 否则不表态 (None)
    """

    experiment_name: str
    """实验唯一名, 给 hash 加盐, 不同实验各自分桶不串扰."""

    groups: list[ABTestGroup] = field(default_factory=list)

    skip_anonymous: bool = True
    """匿名用户 (user_id 为空) 是否跳过实验. 默认跳过."""

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("ABTestRouter 至少需要 1 个 group")
        if any(g.weight < 0 for g in self.groups):
            raise ValueError("ABTestGroup.weight 必须 ≥ 0")
        if sum(g.weight for g in self.groups) <= 0:
            raise ValueError("groups 总权重必须 > 0")

    def _select_group(self, user_id: str) -> ABTestGroup:
        """根据 user_id 稳定选组."""
        h = hashlib.sha256(f"{self.experiment_name}:{user_id}".encode()).hexdigest()
        bucket = int(h[:8], 16)  # 取前 32 bit 作为桶号
        total = sum(g.weight for g in self.groups)
        target = (bucket / 0xFFFFFFFF) * total
        cumulative = 0.0
        for g in self.groups:
            cumulative += g.weight
            if target <= cumulative:
                return g
        return self.groups[-1]  # 浮点尾巴兜底

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None:
        if request.preferred_provider or request.preferred_model:
            return None
        if not available:
            return None
        uid = request.user_id or ""
        if self.skip_anonymous and not uid:
            return None

        group = self._select_group(uid)
        logger.debug(
            "ABTestRouter: experiment=%s user=%s%s → group=%s",
            self.experiment_name,
            uid[:8],
            "***" if uid else "",
            group.name,
        )

        if not group.provider or not group.model:
            return None  # 组不指定 → 让后续 Router 决定

        # 校验该 (provider, model) 在 available 中
        for p, m in available:
            if p == group.provider and m.name == group.model:
                return RoutingDecision(
                    provider=p,
                    model=m.name,
                    reason=f"abtest:{self.experiment_name}:{group.name}",
                )

        logger.warning(
            "ABTestRouter group=%s 指定的 %s:%s 不在 available 中, 跳过",
            group.name,
            group.provider,
            group.model,
        )
        return None


__all__ = ["ABTestGroup", "ABTestRouter"]
