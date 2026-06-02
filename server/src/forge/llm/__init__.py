"""LLM 子系统对外入口.

唯一推荐用法:
    from forge.llm import LLMGateway, LLMRequest, LLMResponse, GatewayBinding, GatewayLLMAdapter

调用语义:
    - 一次性调用 (摘要 / 标题 / 简短补全): get_llm_gateway().complete(LLMRequest(...))
    - 代理风格 (ReActAgent / Summarizer): 构造 GatewayBinding, 通过 GatewayLLMAdapter
      把 gateway 包成兼容 chat/chat_stream/chat_with_tools/chat_with_tools_stream 的对象,
      喂给 agent 即可.

底层组件 (一般无需直接使用):
    - registry: register_llm / build_llm_client / list_providers / split_provider_model
    - pipeline: PipelineRunner + Pre/Post Middleware ABC
"""

from .binding import GatewayBinding, GatewayLLMAdapter
from .gateway import LLMGateway, get_llm_gateway, reset_llm_gateway
from .pipeline import (
    InputValidationError,
    PipelineRunner,
    PostMiddleware,
    PreMiddleware,
)
from .providers.base import LLM, ChatChunk, ChatMessage, ChatResult
from .registry import (
    build_llm_client,
    list_providers,
    register_llm,
    split_provider_model,
)
from .request import CostEstimate, LLMRequest, LLMResponse

__all__ = [
    # 网关入口
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "CostEstimate",
    "get_llm_gateway",
    "reset_llm_gateway",
    # Agent 接入适配
    "GatewayBinding",
    "GatewayLLMAdapter",
    # Pipeline 扩展点
    "PipelineRunner",
    "PreMiddleware",
    "PostMiddleware",
    "InputValidationError",
    # Provider 基础类型
    "LLM",
    "ChatMessage",
    "ChatResult",
    "ChatChunk",
    # 注册表 (Provider 实现侧使用)
    "register_llm",
    "build_llm_client",
    "list_providers",
    "split_provider_model",
]
