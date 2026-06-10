"""验证 chat 透传边界: model_options 里的路由字段不污染 SDK extra_options.

根因: ModelOptionsIn 同时含路由选择 (provider/model) 与采样参数 (thinking/
thinking_level), 整体当 extra_options 透传给 provider SDK 会把 provider 裸传进
chat.completions.create 触发 unexpected keyword。_sdk_model_options 在传给 agent
前剔除路由字段, 只保留采样参数。
"""
from __future__ import annotations

from forge.chat.runner import _sdk_model_options


def test_strips_route_keys() -> None:
    """provider/model 被剔除, 采样参数 thinking/thinking_level 保留."""
    opts = {
        "provider": "mimo",
        "model": "mimo-v2-pro",
        "thinking": True,
        "thinking_level": "high",
    }
    assert _sdk_model_options(opts) == {"thinking": True, "thinking_level": "high"}


def test_none_passthrough() -> None:
    assert _sdk_model_options(None) is None


def test_route_only_becomes_none() -> None:
    """只有路由字段时, 剔除后为空 → 返回 None (不给 SDK 任何透传参数)."""
    assert _sdk_model_options({"provider": "mimo", "model": "x"}) is None


def test_does_not_mutate_input() -> None:
    """不修改入参 (ctx.model_options 需保持完整供 binding/resume 用)."""
    opts = {"provider": "mimo", "model": "x", "thinking": True}
    _sdk_model_options(opts)
    assert opts == {"provider": "mimo", "model": "x", "thinking": True}
