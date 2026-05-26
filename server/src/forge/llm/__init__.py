"""LLM 子系统对外入口.

推荐使用:
    from forge.llm import LLMGateway, LLMRequest, LLMResponse

旧接口兼容 (build_chain_from_settings / register_llm) 仍可用.
"""

from .gateway import (
    LLMGateway,
    build_chain_from_settings,
    build_llm_client,
    build_utility_chain_from_settings,
    get_llm_gateway,
    list_providers,
    register_llm,
    split_provider_model,
)
from .pipeline import (
    InputValidationError,
    PipelineRunner,
    PostMiddleware,
    PreMiddleware,
)
from .providers.base import LLM, ChatChunk, ChatMessage, ChatResult
from .request import CostEstimate, LLMRequest, LLMResponse

__all__ = [
    # 新接口
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "CostEstimate",
    "get_llm_gateway",
    # Pipeline 扩展点
    "PipelineRunner",
    "PreMiddleware",
    "PostMiddleware",
    "InputValidationError",
    # 旧接口 (向后兼容)
    "LLM",
    "ChatMessage",
    "ChatResult",
    "ChatChunk",
    "register_llm",
    "build_llm_client",
    "build_chain_from_settings",
    "build_utility_chain_from_settings",
    "list_providers",
    "split_provider_model",
]
