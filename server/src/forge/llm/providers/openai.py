"""OpenAI-compatible LLM 实现.

覆盖所有兼容 OpenAI Chat Completions API 的服务:
    - openai:    OpenAI 官方 (api.openai.com)
    - deepseek:  DeepSeek (api.deepseek.com)
    - dashscope: 阿里云通义 (dashscope.aliyuncs.com/compatible-mode/v1)

三者均使用同一套 openai SDK, 只是 base_url / api_key 不同.

一个 LLM 实例 = 一个 (provider, api_key) 的 SDK client (含 HTTP 连接池).
model / temperature / thinking 等都是 per-call 参数.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any, AsyncIterator, cast

from openai import NOT_GIVEN, APIStatusError, APITimeoutError, OpenAI

from forge.core.types.message import Message, ToolCall

from ..registry import register_llm
from .base import LLM

logger = logging.getLogger(__name__)


def _log_llm_request(
    llm: OpenAICompatibleLLM,
    payload: list[dict],
    tools: list[dict],
    kwargs: dict[str, Any],
) -> None:
    """DEBUG 级: 打印一次 LLM 请求概要."""
    if not logger.isEnabledFor(logging.DEBUG):
        return

    def _shrink(s: Any, n: int = 200) -> str:
        s = "" if s is None else str(s)
        return s if len(s) <= n else f"{s[:n]}...(+{len(s) - n} chars)"

    msg_brief = [
        {
            "role": m.get("role"),
            "content": _shrink(m.get("content"), 200),
            **({"tool_calls": len(m["tool_calls"])} if m.get("tool_calls") else {}),
            **(
                {"reasoning_content": _shrink(m["reasoning_content"], 100)}
                if m.get("reasoning_content")
                else {}
            ),
        }
        for m in payload
    ]
    logger.debug(
        "LLM 请求 provider=%s model=%s messages=%d tools=%s kwargs=%s payload=%s",
        llm.provider_name,
        kwargs.get("model"),
        len(payload),
        [t.get("function", {}).get("name") for t in tools] or "[]",
        {k: v for k, v in kwargs.items() if k != "model"},
        msg_brief,
    )


class OpenAICompatibleLLM(LLM):
    """OpenAI Chat Completions 兼容实现基类."""

    DEFAULT_BASE_URL: str | None = None  # 子类可覆盖

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        effective_base_url = base_url or self.DEFAULT_BASE_URL
        self._client = OpenAI(
            api_key=api_key,
            base_url=effective_base_url,
            timeout=timeout,
        )
        logger.info(
            "%s 就绪: base_url=%s key=%s",
            type(self).__name__,
            effective_base_url or "openai-default",
            self.api_key_fingerprint,
        )

    @property
    def supports_native_stream(self) -> bool:
        return True

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def _create_chat_completion(self, **kwargs: Any) -> Any:
        create = cast(Any, self._client.chat.completions.create)
        return create(**kwargs)

    # ------------------------------------------------------------------
    # _build_kwargs: 组装 chat.completions.create 的共用参数
    # ------------------------------------------------------------------
    def _build_kwargs(
        self,
        *,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": model,
            "temperature": temperature if temperature is not None else 0.7,
        }
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        if top_p is not None:
            kw["top_p"] = top_p
        # reasoning_effort 只从 extra_options 读取 (per-request 前端传入)
        opts = extra_options or {}
        if opts.get("reasoning_effort"):
            kw["reasoning_effort"] = opts["reasoning_effort"]
        return kw

    # ------------------------------------------------------------------
    # chat_stream: 内容增量
    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list,
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        from .base import ChatChunk

        payload = self._messages_payload(messages)
        kw = self._build_kwargs(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            extra_options=extra_options,
        )
        try:
            stream = self._create_chat_completion(
                messages=payload,
                stream=True,
                stream_options={"include_usage": True},
                **kw,
            )
        except (APIStatusError, APITimeoutError) as e:
            raise RuntimeError(f"{type(self).__name__} 流式调用失败: {e}") from e

        for chunk in stream:
            if not chunk.choices:
                if chunk.usage:
                    yield ChatChunk(
                        delta="",
                        finish_reason="stop",
                        usage=chunk.usage.model_dump(),
                    )
                continue

            choice = chunk.choices[0]
            delta_text = (choice.delta.content or "") if choice.delta else ""
            yield ChatChunk(
                delta=delta_text,
                finish_reason=choice.finish_reason,
                usage=chunk.usage.model_dump() if chunk.usage else None,
            )

    # ------------------------------------------------------------------
    # chat_with_tools: 非流式, 含 tool_calls
    # ------------------------------------------------------------------
    async def chat_with_tools(
        self,
        messages: list,
        tools: list[dict],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict:
        """OpenAI tool calling (非流式)."""
        payload = self._messages_payload(messages)
        kw = self._build_kwargs(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            extra_options=extra_options,
        )
        try:
            resp = self._create_chat_completion(
                messages=payload,
                stream=False,
                tools=tools if tools else NOT_GIVEN,
                tool_choice=tool_choice if tools else NOT_GIVEN,
                **kw,
            )
        except (APIStatusError, APITimeoutError) as e:
            raise RuntimeError(f"{type(self).__name__} tool 调用失败: {e}") from e

        choice = resp.choices[0].message
        tool_calls = self._parse_tool_calls(choice.tool_calls)
        return {
            "content": choice.content or "",
            "tool_calls": tool_calls,
            "usage": resp.usage.model_dump() if resp.usage else {},
            "model": resp.model,
        }

    # ------------------------------------------------------------------
    # chat_with_tools_stream: 真流式 + tool_call 累积
    # ------------------------------------------------------------------
    async def chat_with_tools_stream(
        self,
        messages: list,
        tools: list[dict],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """真流式 tool calling.

        OpenAI 协议: tool call 在多个 chunk 里分片到达, name 一开始给定,
        arguments 是逐字符增量. 这里累积成完整 ToolCall 后再吐出来.
        """
        payload = self._messages_payload(messages)
        kw = self._build_kwargs(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            extra_options=extra_options,
        )
        _log_llm_request(self, payload, tools, kw)
        try:
            stream = self._create_chat_completion(
                messages=payload,
                stream=True,
                stream_options={"include_usage": True},
                tools=tools if tools else NOT_GIVEN,
                tool_choice=tool_choice if tools else NOT_GIVEN,
                **kw,
            )
        except (APIStatusError, APITimeoutError) as e:
            raise RuntimeError(f"{type(self).__name__} tool stream 调用失败: {e}") from e

        tc_buffer: dict[int, dict[str, str]] = {}
        last_model: str | None = None

        for chunk in stream:
            last_model = chunk.model or last_model

            if not chunk.choices:
                if chunk.usage:
                    yield {
                        "content_delta": "",
                        "tool_calls": None,
                        "finish_reason": None,
                        "usage": chunk.usage.model_dump(),
                        "model": last_model,
                    }
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            content_delta = (delta.content or "") if delta and delta.content else ""

            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    buf = tc_buffer.setdefault(idx, {"id": "", "name": "", "arguments_str": ""})
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        buf["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        buf["arguments_str"] += tc_delta.function.arguments

            finish_reason = choice.finish_reason
            emitted_tcs: list[ToolCall] | None = None
            if finish_reason == "tool_calls" and tc_buffer:
                emitted_tcs = []
                for idx in sorted(tc_buffer.keys()):
                    buf = tc_buffer[idx]
                    try:
                        args = json.loads(buf["arguments_str"] or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": buf["arguments_str"]}
                    emitted_tcs.append(ToolCall(id=buf["id"], name=buf["name"], arguments=args))
                tc_buffer.clear()

            yield {
                "content_delta": content_delta,
                "tool_calls": emitted_tcs,
                "finish_reason": finish_reason,
                "usage": chunk.usage.model_dump() if chunk.usage else None,
                "model": last_model,
            }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _messages_payload(self, messages: list) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if isinstance(m, Message):
                out.append(m.to_openai_dict())
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def _parse_tool_calls(raw_tcs) -> list[ToolCall]:
        result: list[ToolCall] = []
        for tc in raw_tcs or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            result.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return result


# @register_llm("openai")
class OpenAILLM(OpenAICompatibleLLM):
    """OpenAI 官方 API."""

    @property
    def provider_name(self) -> str:
        return "openai"


@register_llm("deepseek")
class DeepSeekLLM(OpenAICompatibleLLM):
    """DeepSeek API (OpenAI 兼容) + thinking 模式特化.

    与父类的三点差异 (来自 DeepSeek 官方文档的 thinking 模式契约):
        1. thinking=true 时要在 extra_body 里塞 {"thinking": {"type": "enabled"}}.
        2. 流式响应里 delta.reasoning_content 携带"思考链"内容,
           需要单独累积并实时回吐 (chunk dict 多 reasoning_delta 字段).
        3. 下一轮请求里 assistant 消息必须把 reasoning_content 字段回灌,
           否则 API 返回 400 "must be passed back to the API."
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    @property
    def provider_name(self) -> str:
        return "deepseek"

    def _build_kwargs(
        self,
        *,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        thinking: bool | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kw = super()._build_kwargs(
            model=model, temperature=temperature, max_tokens=max_tokens,
            top_p=top_p, reasoning_effort=reasoning_effort, extra_options=extra_options,
        )
        # thinking 只从 extra_options 读取 (per-request 前端传入)
        opts = extra_options or {}
        effective_thinking = opts.get("thinking")
        if effective_thinking is not None:
            body = kw.setdefault("extra_body", {})
            body["thinking"] = {"type": "enabled" if effective_thinking else "disabled"}
            if not effective_thinking:
                kw.pop("reasoning_effort", None)
        return kw

    def _messages_payload(self, messages: list) -> list[dict]:
        """发请求前把 Message.reasoning_content 塞回 payload (assistant 消息)."""
        payload = super()._messages_payload(messages)
        for orig, d in zip(messages, payload, strict=False):
            if isinstance(orig, Message) and orig.extra_content:
                d["reasoning_content"] = orig.extra_content
        return payload

    async def chat_with_tools_stream(
        self,
        messages: list,
        tools: list[dict],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        thinking: bool | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """DeepSeek thinking 模式的流式 tool calling.

        chunk dict 在父类基础上多一个 reasoning_delta 字段, 携带思考链增量.
        """
        payload = self._messages_payload(messages)
        kw = self._build_kwargs(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            thinking=thinking,
            extra_options=extra_options,
        )
        _log_llm_request(self, payload, tools, kw)
        try:
            stream = self._create_chat_completion(
                messages=payload,
                stream=True,
                tools=tools if tools else NOT_GIVEN,
                tool_choice=tool_choice if tools else NOT_GIVEN,
                **kw,
            )
        except (APIStatusError, APITimeoutError) as e:
            raise RuntimeError(f"DeepSeekLLM tool stream 调用失败: {e}") from e

        tc_buffer: dict[int, dict[str, str]] = {}
        last_model: str | None = None

        for chunk in stream:
            last_model = chunk.model or last_model

            if not chunk.choices:
                if chunk.usage:
                    yield {
                        "content_delta": "",
                        "reasoning_delta": "",
                        "tool_calls": None,
                        "finish_reason": None,
                        "usage": chunk.usage.model_dump(),
                        "model": last_model,
                    }
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            content_delta = (delta.content or "") if delta and delta.content else ""
            reasoning_delta = getattr(delta, "reasoning_content", None) or "" if delta else ""

            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    buf = tc_buffer.setdefault(idx, {"id": "", "name": "", "arguments_str": ""})
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        buf["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        buf["arguments_str"] += tc_delta.function.arguments

            finish_reason = choice.finish_reason
            emitted_tcs: list[ToolCall] | None = None
            if finish_reason == "tool_calls" and tc_buffer:
                emitted_tcs = []
                for idx in sorted(tc_buffer.keys()):
                    buf = tc_buffer[idx]
                    try:
                        args = json.loads(buf["arguments_str"] or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": buf["arguments_str"]}
                    emitted_tcs.append(ToolCall(id=buf["id"], name=buf["name"], arguments=args))
                tc_buffer.clear()

            yield {
                "content_delta": content_delta,
                "reasoning_delta": reasoning_delta,
                "tool_calls": emitted_tcs,
                "finish_reason": finish_reason,
                "usage": chunk.usage.model_dump() if chunk.usage else None,
                "model": last_model,
            }


@register_llm("dashscope")
class DashScopeCompatLLM(OpenAICompatibleLLM):
    """阿里云通义 DashScope (OpenAI 兼容模式)."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def provider_name(self) -> str:
        return "dashscope"
