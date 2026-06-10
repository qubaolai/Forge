"""验证 LLMGateway.complete_structured 的结构化输出编排.

职责边界 (provider 无关):
    - 把 schema 包成 provider 无关的 structured_output 意图塞进 extra_options;
    - 用 jsonschema 校验返回内容; 非法/不合规则回灌纠正并重试;
    - 重试换幂等键 (避免 dedup 短路); 耗尽抛 StructuredOutputError.

这里用桩替换 gateway.complete, 只验证编排逻辑, 不触达 dispatcher/provider.
"""
from __future__ import annotations

import pytest

from forge.core.types.message import Message
from forge.llm.gateway import (
    LLMGateway,
    StructuredOutputError,
    _validate_structured,
)
from forge.llm.request import LLMRequest, LLMResponse

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
    "additionalProperties": False,
}


def _make_gateway() -> LLMGateway:
    # pipeline 传非 None 避免构造默认 Pipeline; settings 不被 complete 桩使用
    return LLMGateway(settings=None, pipeline=None)


async def test_first_success_injects_schema_and_returns_raw() -> None:
    """首次即合规: complete 只调一次, schema 注入正确, 入参未被原地修改."""
    gw = _make_gateway()
    calls: list[LLMRequest] = []

    async def fake_complete(req: LLMRequest) -> LLMResponse:
        calls.append(req)
        return LLMResponse(content='{"a": 1}', model="m")

    gw.complete = fake_complete  # type: ignore[method-assign]
    req = LLMRequest(
        messages=[Message(role="user", content="hi")],
        extra_options={"seed": 7},
    )
    resp = await gw.complete_structured(req, schema=SCHEMA, name="foo")
    print(f"响应内容: {resp}")

    assert len(calls) == 1
    structured = calls[0].extra_options["structured_output"]
    assert structured == {"schema": SCHEMA, "name": "foo", "strict": True}
    assert calls[0].extra_options["seed"] == 7  # 原有键保留
    assert resp.content == '{"a": 1}'  # 原样返回
    # 入参未被原地修改
    assert req.extra_options == {"seed": 7}
    assert len(req.messages) == 1


async def test_reprompts_then_succeeds() -> None:
    """首轮非法 JSON → 回灌纠正 → 二轮合规; 重试换幂等键."""
    gw = _make_gateway()
    outs = ["not json at all", '{"a": 2}']
    calls: list[LLMRequest] = []

    async def fake_complete(req: LLMRequest) -> LLMResponse:
        calls.append(req)
        return LLMResponse(content=outs[len(calls) - 1], model="m")

    gw.complete = fake_complete  # type: ignore[method-assign]
    req = LLMRequest(
        messages=[Message(role="user", content="hi")],
        idempotency_key="k",
    )
    resp = await gw.complete_structured(req, schema=SCHEMA)

    assert len(calls) == 2
    # 第二轮 messages 末尾追加 assistant(错误输出) + user(纠正提示)
    assert len(calls[1].messages) == 3
    assert calls[1].messages[1].role == "assistant"
    assert calls[1].messages[1].content == "not json at all"
    assert calls[1].messages[2].role == "user"
    # 重试换幂等键, 避免 dedup 短路返回上一轮错误结果
    assert calls[0].idempotency_key == "k"
    assert calls[1].idempotency_key == "k:structured:1"
    assert resp.content == '{"a": 2}'


async def test_schema_violation_triggers_reprompt() -> None:
    """合法 JSON 但缺 required 字段, 也应触发回灌 (jsonschema 校验, 非仅 JSON 合法性)."""
    gw = _make_gateway()
    outs = ["{}", '{"a": 3}']  # 首轮合法 JSON 但缺 a
    calls: list[LLMRequest] = []

    async def fake_complete(req: LLMRequest) -> LLMResponse:
        calls.append(req)
        return LLMResponse(content=outs[len(calls) - 1], model="m")

    gw.complete = fake_complete  # type: ignore[method-assign]
    req = LLMRequest(messages=[Message(role="user", content="hi")])
    resp = await gw.complete_structured(req, schema=SCHEMA)

    assert len(calls) == 2
    assert resp.content == '{"a": 3}'


async def test_exhausts_and_raises() -> None:
    """重试耗尽仍不合规: 抛 StructuredOutputError, complete 调 max_retries+1 次."""
    gw = _make_gateway()
    calls: list[LLMRequest] = []

    async def fake_complete(req: LLMRequest) -> LLMResponse:
        calls.append(req)
        return LLMResponse(content="still bad", model="m")

    gw.complete = fake_complete  # type: ignore[method-assign]
    req = LLMRequest(messages=[Message(role="user", content="hi")])
    with pytest.raises(StructuredOutputError) as ei:
        await gw.complete_structured(req, schema=SCHEMA, max_retries=1)

    assert len(calls) == 2  # 1 初始 + 1 重试
    assert ei.value.last_content == "still bad"


def test_validate_structured_cases() -> None:
    """_validate_structured 区分: 合规 / 非法 JSON / 合法但不符 schema."""
    assert _validate_structured('{"a": 1}', SCHEMA) is None
    assert "不是合法 JSON" in (_validate_structured("nope", SCHEMA) or "")
    msg = _validate_structured("{}", SCHEMA)  # 合法 JSON 缺 required a
    assert msg is not None and "schema" in msg
