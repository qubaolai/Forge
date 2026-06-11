"""对外 LLM 网关端点 (/v1/llm/chat/completions) 单测.

不跑真 LLMGateway (会触发模型调用); 只验路由内的纯函数:
    1. _build_request 的 model 三种形态解析 (空 / 档位 / provider:model)
    2. _to_messages 的 OpenAI dict -> Message 转换 (含 tool_calls 回灌)
    3. _tool_calls_to_openai 的 ToolCall -> OpenAI 数组转换
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from forge.api.routes.v1.llm import (
    _build_request,
    _to_messages,
    _tool_calls_to_openai,
)
from forge.api.schemas.llm import LLMChatCompletionIn, LLMChatMessageIn
from forge.core.types.message import ToolCall


def _body(**overrides) -> LLMChatCompletionIn:
    base = {"messages": [{"role": "user", "content": "你好"}]}
    base.update(overrides)
    return LLMChatCompletionIn(**base)


def test_build_request_model_empty_uses_default_chain():
    req = _build_request(_body(), user_id="1")
    assert req.model_profile is None
    assert req.preferred_provider is None
    assert req.preferred_model is None
    assert req.user_id == "1"


def test_build_request_model_profile():
    req = _build_request(_body(model="fast"), user_id="1")
    assert req.model_profile == "fast"
    assert req.preferred_provider is None


def test_build_request_model_pin_provider_model():
    req = _build_request(_body(model="dashscope:qwen-plus"), user_id="1")
    assert req.preferred_provider == "dashscope"
    assert req.preferred_model == "qwen-plus"
    assert req.model_profile is None


def test_build_request_model_invalid_raises_422():
    with pytest.raises(HTTPException) as ei:
        _build_request(_body(model="gpt-4o"), user_id="1")
    assert ei.value.status_code == 422


def test_build_request_tools_set_task_type():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    req = _build_request(_body(tools=tools), user_id="1")
    assert req.task_type == "tool_use"
    assert req.requires_tools is True


def test_to_messages_converts_tool_call_roundtrip():
    items = [
        LLMChatMessageIn(role="user", content="查天气"),
        LLMChatMessageIn(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city": "北京"}'},
                }
            ],
        ),
        LLMChatMessageIn(role="tool", content="晴", tool_call_id="call_1", name="weather"),
    ]
    msgs = _to_messages(items)
    assert msgs[1].tool_calls[0].name == "weather"
    assert msgs[1].tool_calls[0].arguments == {"city": "北京"}
    assert msgs[2].tool_call_id == "call_1"


def test_tool_calls_to_openai_serializes_arguments():
    out = _tool_calls_to_openai([ToolCall(id="c1", name="f", arguments={"a": 1})])
    assert out[0]["id"] == "c1"
    assert out[0]["function"]["name"] == "f"
    assert json.loads(out[0]["function"]["arguments"]) == {"a": 1}
