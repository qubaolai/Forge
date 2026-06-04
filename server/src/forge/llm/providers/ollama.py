"""Ollama 本地 LLM 实现.

Ollama 暴露 OpenAI 兼容端点 (/v1/chat/completions), 因此直接复用
OpenAICompatibleLLM, 仅需指定默认 base_url 与 provider 名.

要点:
    - 默认指向本机 http://localhost:11434/v1; 远程部署时由 providers.base_url
      覆盖 (经 lifespan 预热的 client_options 注入).
    - Ollama 本地服务通常无鉴权, 但 LLM 池/openai SDK 仍以非空 api_key 为索引,
      因此使用方需注册一个占位 key (任意非空字符串, 如 "ollama"), Ollama 会忽略它.
    - 是否启用 tool calling / thinking 由 DB 模型配置的 capabilities 决定,
      类层面保持父类默认即可.
"""

from __future__ import annotations

from ..registry import register_llm
from .openai import OpenAICompatibleLLM


@register_llm("ollama")
class OllamaLLM(OpenAICompatibleLLM):
    """Ollama 本地 LLM (OpenAI 兼容模式)."""

    DEFAULT_BASE_URL = "http://localhost:11434/v1"  # provider 行未填 base_url 时的兜底

    @property
    def provider_name(self) -> str:
        return "ollama"
