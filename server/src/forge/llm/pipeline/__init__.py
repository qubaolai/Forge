"""LLM Pre/Post Pipeline 中间件.

默认 middleware 注册顺序见 LLMGateway._default_pipeline:
    Pre (按短路优先):
        1. InputValidatorMiddleware     — 消息数量 / 大小校验
        2. InboundRateLimitMiddleware   — per-user RPM/TPM 滑动窗口
        3. BudgetMiddleware             — 网关级预算 pre-flight
        4. DeduplicationMiddleware      — 幂等键去重
        5. ExactCacheMiddleware         — 精确缓存命中即短路
    Post (每个都执行):
        1. CacheWriteMiddleware         — 写精确缓存
        2. DedupCompleteMiddleware      — 幂等完成登记
        3. AuditMiddleware              — 请求级摘要日志
"""

from __future__ import annotations

from .audit import AuditMiddleware
from .base import PipelineRunner, PostMiddleware, PreMiddleware
from .budget import BudgetMiddleware
from .cache import CacheWriteMiddleware, ExactCacheMiddleware
from .dedup import (
    DedupCompleteMiddleware,
    DeduplicationMiddleware,
    IdempotencyStore,
    get_idempotency_store,
    set_idempotency_store,
)
from .rate_limit import InboundRateLimitMiddleware
from .validator import InputValidationError, InputValidatorMiddleware

__all__ = [
    "AuditMiddleware",
    "BudgetMiddleware",
    "CacheWriteMiddleware",
    "DedupCompleteMiddleware",
    "DeduplicationMiddleware",
    "ExactCacheMiddleware",
    "IdempotencyStore",
    "InboundRateLimitMiddleware",
    "InputValidationError",
    "InputValidatorMiddleware",
    "PipelineRunner",
    "PostMiddleware",
    "PreMiddleware",
    "get_idempotency_store",
    "set_idempotency_store",
]
