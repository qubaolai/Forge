"""LLM 抽象基类.

设计:
    - 一个 LLM 实例 = 一个 (impl, api_key) 的 SDK client (含 HTTP 连接池)
    - model / temperature / thinking 等都是 per-call 参数, 在每次调用时传入
    - 同一 client 可被不同 model 调用复用

接口分两类:
    - chat() / chat_with_tools():        非流式
    - chat_stream() / chat_with_tools_stream(): 流式

非流式方法在基类提供默认实现 (走对应的 *_stream 然后聚合),
具体 provider 实现真流式时不需要重复写非流式版本.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatResult:
    """非流式结果."""

    content: str
    model: str
    usage: dict = field(default_factory=dict)
    raw: dict | None = None


@dataclass
class ChatChunk:
    """流式增量块.

    delta:         本次增量的内容. 可能为空字符串 (心跳/起始包)
    finish_reason: 结束原因, 仅最后一块非 None ("stop" / "length" / 其他)
    usage:         token 用量, 通常只在最后一块出现
    raw:           原始 SDK 返回, 调试用
    """

    delta: str = ""
    finish_reason: str | None = None
    usage: dict | None = None
    raw: dict | None = None

    @property
    def is_final(self) -> bool:
        return self.finish_reason is not None


class LLM(ABC):
    """LLM client 抽象 - 一个实例对应一个 (impl, api_key) 的 SDK client.

    生命周期:
        - 由 LLMClientPool 在启动期或首次调用时创建
        - 进程内长期复用, 持有 SDK 的 HTTP 连接池
        - api_key 入参绑死, model / temperature 每次调用传入

    构造参数:
        api_key:  必填, provider 接入 token
        base_url: provider 自定义端点 (OpenAI 兼容类常用)
        timeout:  HTTP 请求超时秒数

    chat 类方法的通用 kwargs:
        model:             必传, 调用时指定具体 model
        temperature:       可选, 默认由具体 provider 决定
        max_tokens:        可选
        top_p:             可选
        thinking:          可选 bool, 部分 provider 支持思考模式
        reasoning_effort:  可选, 推理深度
        extra_options:     前端 per-request 覆盖 (优先级最高)
        **kwargs:          provider 特有字段透传, 不认识的应静默忽略
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError(f"{type(self).__name__} 需要 api_key")
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    def api_key_fingerprint(self) -> str:
        """脱敏的 key 指纹, 日志/监控用."""
        return f"{self._api_key[:6]}***" if self._api_key else "-"

    @property
    def supports_native_stream(self) -> bool:
        """该 provider 是否实现了真流式. 默认 False, 子类按需 override."""
        return False

    @property
    def supports_tool_calling(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # chat: 非流式 (默认走 chat_stream 聚合)
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """非流式 chat. 默认实现: 调 chat_stream 后聚合."""
        chunks: list[ChatChunk] = []
        stream = self.chat_stream(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=extra_options,
            **kwargs,
        )
        # chat_stream 可能是 async generator 或 sync generator, 兼容两种形态
        if isinstance(stream, AsyncIterator):
            async for chunk in stream:
                chunks.append(chunk)
        else:
            for chunk in stream:
                chunks.append(chunk)
        content = "".join(c.delta for c in chunks)
        usage = next((c.usage for c in reversed(chunks) if c.usage), {})
        return ChatResult(content=content, model=model, usage=usage or {})

    # ------------------------------------------------------------------
    # chat_stream: 流式 (子类必实现)
    # ------------------------------------------------------------------
    @abstractmethod
    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk] | Iterator[ChatChunk]: ...

    # ------------------------------------------------------------------
    # Tool calling: 非所有 provider 实现 (默认抛 NotImplementedError)
    # ------------------------------------------------------------------
    async def chat_with_tools(
        self,
        messages: list,
        tools: list[dict],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict:
        """带 tool calling 的 chat. 子类按需 override.

        返回 {"content": str, "tool_calls": list[ToolCall], "usage": dict, "model": str}
        """
        raise NotImplementedError(f"{type(self).__name__} 未实现 chat_with_tools.")

    async def chat_with_tools_stream(
        self,
        messages: list,
        tools: list[dict],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """带 tool calling 的流式 chat.

        默认实现退回非流式 chat_with_tools, 保证调用面可用.
        子类要支持真流式时 override 此方法.

        chunk dict 形态:
            {
                "content_delta": str,
                "tool_calls": list[ToolCall] | None,   # 仅最终块出现
                "finish_reason": str | None,
                "usage": dict | None,
                "model": str | None,
            }
        """
        resp = await self.chat_with_tools(
            messages,
            tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            extra_options=extra_options,
            **kwargs,
        )
        yield {
            "content_delta": resp.get("content", ""),
            "tool_calls": resp.get("tool_calls") or [],
            "finish_reason": "tool_calls" if resp.get("tool_calls") else "stop",
            "usage": resp.get("usage") or {},
            "model": resp.get("model") or model,
        }
