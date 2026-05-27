"""延迟感知路由 (软约束).

基于进程内 EMA (指数加权移动平均) 维护 (provider, model) 的延迟统计.
决策规则:
    1. preferred_provider/model 存在 → 不介入
    2. 所有候选都没有历史数据 → 不表态 (返回 None)
    3. 选 EMA 最低的候选, 但不强制: 当最低 EMA × penalty_ratio < 其他候选时才换
       (避免抖动导致频繁切换)

数据更新:
    由 AuditMiddleware 在每次成功调用后调 update(provider, model, latency_s).
    Phase 6 可考虑迁移至 Redis (跨进程共享 EMA).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from .base import Candidate, Router, RoutingDecision, RoutingRequest

logger = logging.getLogger(__name__)


@dataclass
class LatencyAwareRouter(Router):
    """EMA 延迟统计 + 软优先级降权."""

    alpha: float = 0.2
    """EMA 平滑系数. 越大对新样本越敏感; 0.2 ≈ 最近 5 次的均值."""

    penalty_ratio: float = 1.3
    """候选 EMA / 最低 EMA ≥ 此比例时降权. 默认 1.3 = 慢 30% 才换."""

    _ema: dict[tuple[str, str], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, provider: str, model: str, latency_s: float) -> None:
        """更新 (provider, model) 的延迟 EMA. 由 AuditMiddleware 在成功后调用."""
        if latency_s <= 0:
            return
        key = (provider, model)
        with self._lock:
            prev = self._ema.get(key)
            if prev is None:
                self._ema[key] = latency_s
            else:
                self._ema[key] = self.alpha * latency_s + (1 - self.alpha) * prev

    def ema_of(self, provider: str, model: str) -> float | None:
        return self._ema.get((provider, model))

    def reset(self) -> None:
        """测试用."""
        with self._lock:
            self._ema.clear()

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None:
        if request.preferred_provider or request.preferred_model:
            return None
        if not available:
            return None

        with self._lock:
            scored: list[tuple[Candidate, float | None]] = [
                ((p, m), self._ema.get((p, m.name))) for p, m in available
            ]

        known = [(c, ema) for c, ema in scored if ema is not None]
        if not known:
            return None

        # 选 EMA 最低的候选
        best_candidate, best_ema = min(known, key=lambda x: x[1])
        # 如果第一个候选已经"足够好" (没有显著比 best 慢), 保留原顺序避免抖动
        first_candidate, first_ema = scored[0]
        if first_ema is not None and first_ema <= best_ema * self.penalty_ratio:
            return None  # 让 RuleBasedRouter / 默认顺序生效

        p, m = best_candidate
        return RoutingDecision(
            provider=p,
            model=m.name,
            reason=f"latency:ema={best_ema:.3f}s",
        )


# 全局单例 (AuditMiddleware 用同一份)
_latency_router: LatencyAwareRouter | None = None
_init_lock = threading.Lock()


def get_latency_router() -> LatencyAwareRouter:
    global _latency_router
    if _latency_router is None:
        with _init_lock:
            if _latency_router is None:
                _latency_router = LatencyAwareRouter()
    return _latency_router


__all__ = ["LatencyAwareRouter", "get_latency_router"]
