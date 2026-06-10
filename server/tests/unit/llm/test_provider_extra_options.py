"""验证 provider 的 extra_options 透传/隔离行为.

新契约 (openai 系): 不再维护白名单, extra_options 里的字段直接透传给 SDK
(response_format / seed / stop / frequency_penalty ...); 仅排除 Forge 内部抽象键
(reasoning_effort / thinking_level / thinking / thinking_budget), 这些由 _build_kwargs
显式消费 (规范化或转 extra_body), 不能原样透传否则 SDK 报 unexpected keyword.
"""
from __future__ import annotations

import pytest

from forge.llm.providers.mock import MockLLM
from forge.llm.providers.openai import DeepSeekLLM, OpenAILLM


class _StopCreate(Exception):
    """测试用: 在 _create_chat_completion 处中断, 捕获已组装的 SDK kwargs."""


def test_openai_passes_through_config_options() -> None:
    """OpenAI provider 应直接透传配置参数 (response_format / seed 等)."""
    llm = OpenAILLM(api_key="sk-test")
    kw = llm._build_kwargs(
        model="gpt-4o",
        temperature=0.0,
        max_tokens=100,
        extra_options={
            "response_format": {"type": "json_object"},
            "seed": 42,
            "frequency_penalty": 0.5,
            "reasoning_effort": "high",
        },
    )
    # 业务/SDK 参数直接透传
    assert kw["response_format"] == {"type": "json_object"}
    assert kw["seed"] == 42
    assert kw["frequency_penalty"] == 0.5
    # 内部抽象键: 规范化后塞 kw, 但不原样透传 (这里 high 已是规范值)
    assert kw["reasoning_effort"] == "high"


def test_openai_excludes_internal_keys() -> None:
    """内部控制键不得原样透传给标准 OpenAI SDK."""
    llm = OpenAILLM(api_key="sk-test")
    kw = llm._build_kwargs(
        model="gpt-4o",
        temperature=0.0,
        max_tokens=100,
        extra_options={"thinking": True, "thinking_level": "low", "thinking_budget": 5000},
    )
    assert "thinking" not in kw
    assert "thinking_level" not in kw
    assert "thinking_budget" not in kw
    # thinking_level 作为 reasoning_effort 别名被规范化消费
    assert kw.get("reasoning_effort") == "low"


def test_deepseek_thinking_to_extra_body_coexists_with_passthrough() -> None:
    """DeepSeek 子类: thinking 进 extra_body, 同时其它配置参数照常透传."""
    llm = DeepSeekLLM(api_key="sk-test")
    kw = llm._build_kwargs(
        model="deepseek-reasoner",
        temperature=0.0,
        max_tokens=100,
        extra_options={"thinking": True, "response_format": {"type": "json_object"}},
    )
    assert kw["extra_body"]["thinking"] == {"type": "enabled"}
    assert kw["response_format"] == {"type": "json_object"}
    assert "thinking" not in kw  # 仅在 extra_body 内


# ----------------------------------------------------------------------
# 结构化输出: schema → provider 原生参数 (下沉到 provider)
# ----------------------------------------------------------------------
def test_openai_build_structured_options() -> None:
    """OpenAI 系: build_structured_options 生成 json_schema 强约束 response_format."""
    llm = OpenAILLM(api_key="sk-test")
    opts = llm.build_structured_options({"type": "object"}, name="foo", strict=True)
    assert opts == {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "foo",
                "schema": {"type": "object"},
                "strict": True,
            },
        }
    }


def test_openai_consumes_structured_output() -> None:
    """_build_kwargs 把 structured_output 意图翻译成 response_format, 内部键不透传 SDK."""
    llm = OpenAILLM(api_key="sk-test")
    kw = llm._build_kwargs(
        model="gpt-4o",
        temperature=0.0,
        max_tokens=100,
        extra_options={
            "structured_output": {
                "schema": {"type": "object"},
                "name": "r",
                "strict": True,
            }
        },
    )
    assert kw["response_format"]["type"] == "json_schema"
    assert kw["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert "structured_output" not in kw  # 内部键不原样透传给 SDK


def test_base_build_structured_options_noop() -> None:
    """未 override 的 provider: build_structured_options 默认 no-op (返回空)."""
    llm = MockLLM(api_key="x")
    assert llm.build_structured_options({"type": "object"}) == {}


async def test_openai_dict_tool_choice_passthrough(monkeypatch) -> None:
    """tool_choice 传 dict (强制指定某工具) 时透传进 SDK kwargs."""
    llm = OpenAILLM(api_key="sk-test")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise _StopCreate()

    monkeypatch.setattr(llm, "_create_chat_completion", fake_create)
    tc = {"type": "function", "function": {"name": "foo"}}
    tools = [{"type": "function", "function": {"name": "foo"}}]
    with pytest.raises(_StopCreate):
        await llm.chat_with_tools([], tools, model="gpt-4o", tool_choice=tc)
    assert captured["tool_choice"] == tc
