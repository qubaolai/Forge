"""LLM 调用方依赖的抽象契约."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from forge.core.types.message import Message


class ToolCallingLLM(ABC):
    """ReAct 只依赖已绑定模型配置的 tool-calling facade."""

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> dict: ...

    @abstractmethod
    def chat_with_tools_stream(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...


__all__ = ["ToolCallingLLM"]
