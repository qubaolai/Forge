"""Router Protocol + 路由请求/决策类型.

Router 决定:在一组候选 (provider, model) 中选哪个作为 primary 给 LLMDispatcher.
注意:
    - Router 只决定 primary，运行时 provider/model 不再配置 fallback。
    - Router 链按优先级串联 (CompositeRouter), 每个 Router.route 返回 None
      表示"我无意见, 交给下一个". 全部 None → CompositeRouter 走默认 (第一个候选).
    - 用户显式 pin (LLMRequest.preferred_provider/preferred_model 双值齐全)
      完全绕过 router, 由 LLMGateway._resolve_provider_model 短路.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from forge.config.domains.llm import ModelConfig


@dataclass(frozen=True)
class RoutingRequest:
    """路由决策所需输入.

    由 LLMGateway 从 LLMRequest 字段构造, 喂给 CompositeRouter.route().
    """

    task_type: str = "chat"
    """chat | tool_use | summary | embedding ..."""

    estimated_input_tokens: int = 0
    """估算输入 token 数, 用于过滤 context_window 不够的 model."""

    requires_tools: bool = False
    requires_vision: bool = False
    requires_thinking: bool = False

    user_id: str | None = None
    session_id: str | None = None

    preferred_provider: str | None = None
    """前端 / agent 配置显式倾向的 provider. RuleBasedRouter 会优先选."""

    preferred_model: str | None = None
    """前端 / agent 配置显式倾向的 model."""

    extra: dict = field(default_factory=dict)
    """provider 特有路由信息 (例如租户 hint), 不影响默认规则."""


@dataclass(frozen=True)
class RoutingDecision:
    """单个 Router 的决策结果."""

    provider: str
    model: str
    reason: str
    """简短日志字符串, 例如 'rule:requires_tools' / 'fallback:default'."""


Candidate = tuple[str, "ModelConfig"]
"""路由候选项: (provider 名, model 配置). available 列表的每一项."""


@runtime_checkable
class Router(Protocol):
    """Router 接口.

    返回 None 表示"无意见", 让 CompositeRouter 询问下一个 Router.
    """

    def route(
        self,
        request: RoutingRequest,
        available: list[Candidate],
    ) -> RoutingDecision | None: ...
