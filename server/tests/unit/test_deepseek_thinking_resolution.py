"""DeepSeekLLM thinking — 只从 extra_options (per-request 前端传入) 读取。"""

from __future__ import annotations

from forge.llm.providers.openai import DeepSeekLLM


def _make_llm() -> DeepSeekLLM:
    return DeepSeekLLM(api_key="test")


def _thinking_body(llm, *, extra_options=None):
    kw = llm._build_kwargs(
        model="deepseek-chat", temperature=None, max_tokens=None,
        extra_options=extra_options,
    )
    return kw.get("extra_body", {}).get("thinking")


def _effort(llm, *, extra_options=None):
    kw = llm._build_kwargs(
        model="deepseek-chat", temperature=None, max_tokens=None,
        extra_options=extra_options,
    )
    return kw.get("reasoning_effort")


# extra_options.thinking=True → enabled
def test_extra_options_true_enabled() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, extra_options={"thinking": True}) == {"type": "enabled"}


# extra_options.thinking=False → disabled
def test_extra_options_false_disabled() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, extra_options={"thinking": False}) == {"type": "disabled"}


# 未传 extra_options → 不设置 thinking
def test_no_extra_options_no_thinking() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, extra_options=None) is None


# extra_options 缺少 thinking key → 不设置
def test_extra_lacks_thinking_key() -> None:
    llm = _make_llm()
    assert _thinking_body(llm, extra_options={"temperature": 0.5}) is None


def test_thinking_level_high_maps_to_high() -> None:
    llm = _make_llm()
    assert _effort(llm, extra_options={"thinking": True, "thinking_level": "high"}) == "high"


def test_thinking_level_xhigh_maps_to_max() -> None:
    llm = _make_llm()
    assert _effort(llm, extra_options={"thinking": True, "thinking_level": "xhigh"}) == "max"
