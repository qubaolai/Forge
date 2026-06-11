"""对外 LLM 网关端点的请求 schema。

请求体与 OpenAI Chat Completions 兼容 (子集), 便于 CLI / 第三方客户端
直接复用 openai 系 SDK 接入; 响应同样输出 OpenAI 风格 JSON / SSE chunk。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LLMChatMessageIn(BaseModel):
    """OpenAI 格式的单条消息。

    assistant 消息可带 tool_calls (回灌上一轮模型的工具调用);
    tool 消息需带 tool_call_id (关联调用结果)。
    """

    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class LLMChatCompletionIn(BaseModel):
    """POST /v1/llm/chat/completions 请求体。

    model 三种形态:
        - 空           → 走系统默认链 (settings.llm)
        - "fast|smart|strong" → 档位链 (跨 provider fallback)
        - "provider:model"    → 显式 pin (对话链, 同 provider 后备)
    """

    model: str | None = None
    messages: list[LLMChatMessageIn] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] = "auto"
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    # 透传给 provider SDK 的额外超参 (top_p / presence_penalty / thinking ...)
    extra_options: dict[str, Any] | None = None
    # 幂等键: 客户端重试时防止 LLM 被重复调用计费
    idempotency_key: str | None = None


__all__ = ["LLMChatCompletionIn", "LLMChatMessageIn"]
