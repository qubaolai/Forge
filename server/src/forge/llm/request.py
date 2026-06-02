"""LLMGateway 的请求/响应数据模型.

业务层只感知这些类型, 不感知内部 dispatcher / chain / provider 等概念.

设计原则:
    - LLMRequest 是 LLMGateway 的唯一输入入口, 承载所有路由提示/横切关注点
    - LLMResponse 在 ChatResult 基础上附加网关元信息 (cache_hit / provider / cost)
    - CostEstimate 用于 estimate_cost() 不实际调用 LLM 的成本预估
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .providers.base import ChatChunk


@dataclass
class LLMRequest:
    """LLMGateway 调用入口.

    路由优先级 (高 → 低):
        preferred_provider/model (显式 pin)
        > model_profile + task_type (Router 决策)
        > 配置默认 (CompositeRouter 兜底)
    """

    messages: list[Any]
    tools: list[dict] | None = None
    tool_choice: str = "auto"

    # ------------------------------------------------------------------
    # 路由提示
    # ------------------------------------------------------------------
    model_profile: str | None = None
    """fast | smart | strong, 任务级模型档位"""

    preferred_provider: str | None = None
    """显式 pin provider, Router 短路"""

    preferred_model: str | None = None
    """显式 pin model"""

    task_type: str = "chat"
    """chat | tool_use | utility | summary | embedding ..."""

    requires_thinking: bool = False
    requires_vision: bool = False
    requires_tools: bool = False

    # ------------------------------------------------------------------
    # Per-call 覆盖
    # ------------------------------------------------------------------
    temperature: float | None = None
    max_tokens: int | None = None
    extra_options: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 网关横切关注点
    # ------------------------------------------------------------------
    user_id: str | None = None
    """限额 / 成本归属, 由调用方注入"""

    idempotency_key: str | None = None
    """幂等键: 防止客户端重试导致 LLM 被调用两次"""

    cache_enabled: bool = True
    """精确缓存开关 (即使 enabled, 也需 temperature=0 才会真正缓存)"""

    priority: int = 0
    """0=普通, 1=高优先级, 未来请求队列用"""

    timeout_ms: int | None = None
    """覆盖全局 total_timeout (毫秒)"""

    estimated_input_tokens: int = 0
    """估算输入 token 数 (Router/限额用), 0 表示未估算"""

    extra: dict[str, Any] = field(default_factory=dict)
    """provider 特有路由信息 / 业务扩展字段"""


@dataclass
class LLMResponse:
    """LLMGateway 的非流式响应.

    在 provider 原始 ChatResult 基础上, 附加网关层元信息.
    """

    content: str
    model: str
    provider: str = ""

    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    tool_calls: list[dict] | None = None
    raw: dict | None = None

    # ------------------------------------------------------------------
    # 网关附加信息
    # ------------------------------------------------------------------
    cache_hit: bool = False
    cache_type: str | None = None
    """exact | prompt_native | None"""

    cost_usd: float = 0.0
    """本次调用成本, 已扣除缓存命中部分"""

    latency_ms: float = 0.0
    fallback_position: int = 0
    """0=主模型, ≥1=被 fallback 到第几个备用"""


@dataclass
class CostEstimate:
    """estimate_cost() 不调用 LLM, 仅做成本预估."""

    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_usd: float
    model: str
    provider: str


# 流式响应类型别名 (chunk 形态保持与 ChatChunk 兼容)
LLMStream = AsyncIterator[ChatChunk]
LLMToolStream = AsyncIterator[dict[str, Any]]


__all__ = [
    "LLMRequest",
    "LLMResponse",
    "CostEstimate",
    "LLMStream",
    "LLMToolStream",
]
