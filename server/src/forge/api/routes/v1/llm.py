"""对外 LLM 网关端点 (供 CLI / 第三方客户端直连调用 LLM)。

定位:
    - 服务端智能体编排只服务 Web Chat; CLI 形态的 agent loop 在客户端本地运行,
      服务端仅以本端点提供「认证 + 模型治理 + 网关化 LLM 调用」。
    - 鉴权走 X-API-Key (ApiKeyUser), 配额 / 预算 / 限流 / 缓存 / 审计
      由 LLMGateway 的 Pre/Post 中间件按 user_id 自动生效。

接口形态:
    POST /v1/llm/chat/completions  — OpenAI Chat Completions 兼容 (子集),
    支持非流式 JSON、流式 SSE (含 function calling), 响应不包 success 信封,
    以便客户端直接复用 openai 系 SDK。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from forge.api.dependencies import ApiKeyUser
from forge.api.schemas.llm import LLMChatCompletionIn
from forge.core.types.message import Message, ToolCall
from forge.llm import get_llm_gateway
from forge.llm.request import LLMRequest

router = APIRouter(prefix="/llm", tags=["llm"])

_MODEL_PROFILES = ("fast", "smart", "strong")


def _to_messages(items: list) -> list[Message]:
    """OpenAI 格式消息 → 内部 Message (含 assistant tool_calls 回灌与 tool 结果)。"""
    out: list[Message] = []
    for m in items:
        tool_calls: list[ToolCall] = []
        for tc in m.tool_calls or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            else:
                args = raw_args or {}
            tool_calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )
        out.append(
            Message(
                role=m.role,
                content=m.content or "",
                tool_calls=tool_calls,
                tool_call_id=m.tool_call_id,
                name=m.name,
            )
        )
    return out


def _tool_calls_to_openai(tool_calls: Any) -> list[dict]:
    """内部 ToolCall (或 provider 原始 dict) → OpenAI tool_calls 数组。"""
    result: list[dict] = []
    for tc in tool_calls or []:
        if isinstance(tc, ToolCall):
            result.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
            )
        elif isinstance(tc, dict):
            result.append(tc)
    return result


def _build_request(body: LLMChatCompletionIn, user_id: str) -> LLMRequest:
    """请求体 → LLMRequest, 解析 model 三种形态并注入成本归属。"""
    model_profile: str | None = None
    preferred_provider: str | None = None
    preferred_model: str | None = None

    model = (body.model or "").strip()
    if model:
        if ":" in model:
            preferred_provider, preferred_model = model.split(":", 1)
        elif model in _MODEL_PROFILES:
            model_profile = model
        else:
            raise HTTPException(
                status_code=422,
                detail=f"model 须为空、档位名 {_MODEL_PROFILES} 或 'provider:model' 格式, 收到: {model!r}",
            )

    return LLMRequest(
        messages=_to_messages(body.messages),
        tools=body.tools,
        tool_choice=body.tool_choice,
        model_profile=model_profile,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        task_type="tool_use" if body.tools else "chat",
        requires_tools=bool(body.tools),
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        extra_options=body.extra_options,
        user_id=user_id,
        idempotency_key=body.idempotency_key,
    )


def _chunk_envelope(completion_id: str, model: str, delta: dict, finish_reason: str | None = None) -> dict:
    """组装 OpenAI chat.completion.chunk 信封。"""
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _sse(data: dict | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n".encode()


@router.post("/chat/completions")
async def chat_completions(body: LLMChatCompletionIn, user: ApiKeyUser):
    """网关化 LLM 调用入口 (OpenAI 兼容)。

    - 非流式: 返回 chat.completion JSON (附带网关元信息 cost_usd / cache_hit / provider)。
    - 流式: SSE 输出 chat.completion.chunk, 末尾 data: [DONE];
      带 tools 时工具调用在最终块以完整 delta.tool_calls 一次性给出
      (网关层已聚合, 不做增量切片)。
    """
    gateway = get_llm_gateway()
    req = _build_request(body, user_id=str(user.id))

    if not body.stream:
        resp = await gateway.complete_with_tools(req) if body.tools else await gateway.complete(req)
        message: dict[str, Any] = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            message["tool_calls"] = _tool_calls_to_openai(resp.tool_calls)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": resp.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": resp.finish_reason or "stop",
                }
            ],
            "usage": resp.usage or {},
            # 网关附加元信息 (OpenAI 之外的扩展字段, SDK 会忽略)
            "forge": {
                "provider": resp.provider,
                "cache_hit": resp.cache_hit,
                "cost_usd": resp.cost_usd,
                "fallback_position": resp.fallback_position,
            },
        }

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    model_label = body.model or ""

    async def _stream():
        usage: dict | None = None
        finish: str | None = "stop"
        if body.tools:
            # 流式 + function calling: content_delta 逐块下发, tool_calls 仅最终块出现
            async for chunk in gateway.stream_with_tools(req):
                delta_text = chunk.get("content_delta") or ""
                if delta_text:
                    yield _sse(_chunk_envelope(completion_id, model_label, {"content": delta_text}))
                if chunk.get("tool_calls"):
                    yield _sse(
                        _chunk_envelope(
                            completion_id,
                            model_label,
                            {"tool_calls": _tool_calls_to_openai(chunk["tool_calls"])},
                        )
                    )
                    finish = "tool_calls"
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if chunk.get("finish_reason"):
                    finish = chunk["finish_reason"]
        else:
            async for chunk in gateway.stream(req):
                if chunk.delta:
                    yield _sse(_chunk_envelope(completion_id, model_label, {"content": chunk.delta}))
                if chunk.usage:
                    usage = chunk.usage
                if chunk.finish_reason:
                    finish = chunk.finish_reason
        final = _chunk_envelope(completion_id, model_label, {}, finish_reason=finish)
        if usage:
            final["usage"] = usage
        yield _sse(final)
        yield _sse("[DONE]")

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
