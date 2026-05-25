"""ToolResultPolicy 单测."""

from __future__ import annotations

from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.tool_policy.evicting import EvictingPolicy
from forge.context_mgmt.tool_policy.summarizing import SummarizingPolicy
from forge.context_mgmt.tool_policy.truncating import TruncatingPolicy
from forge.context_mgmt.tool_policy.verbatim import VerbatimPolicy
from forge.llm.token_counter import HeuristicCounter


def _meter():
    return DefaultTokenMeter(HeuristicCounter())


# ---------------------------------------------------------------------------
# VerbatimPolicy: 原样返回
# ---------------------------------------------------------------------------
def test_verbatim_keeps_original():
    p = VerbatimPolicy()
    content = "any tool output content " * 100
    out = p.process("read_file", content, token_budget=999, meter=_meter())
    assert out == content


# ---------------------------------------------------------------------------
# TruncatingPolicy: 超阈值截断
# ---------------------------------------------------------------------------
def test_truncating_below_threshold_keeps_original():
    p = TruncatingPolicy(max_tokens=500)
    content = "short result"
    out = p.process("read_file", content, token_budget=500, meter=_meter())
    assert out == content


def test_truncating_above_threshold_cuts_and_appends_marker():
    p = TruncatingPolicy(max_tokens=50)
    big = "x" * 10_000
    out = p.process("read_file", big, token_budget=50, meter=_meter())
    assert len(out) < len(big)
    assert "截断" in out
    assert "read_file" in out


# ---------------------------------------------------------------------------
# EvictingPolicy: 替换为占位符
# ---------------------------------------------------------------------------
def test_evicting_replaces_with_placeholder():
    p = EvictingPolicy()
    out = p.process("write_file", "随便什么内容", token_budget=0, meter=_meter())
    assert "write_file" in out
    assert "已执行" in out
    assert "随便什么内容" not in out


# ---------------------------------------------------------------------------
# SummarizingPolicy: 阶段 2 兜底走 Truncating
# ---------------------------------------------------------------------------
def test_summarizing_falls_back_to_truncating():
    p = SummarizingPolicy(summarize_threshold=100, fallback_max_tokens=20)
    big = "y" * 5_000
    out = p.process("read_file", big, token_budget=20, meter=_meter())
    # 阶段 2 当前实现降级到 Truncating, 应该看到截断标记
    assert len(out) < len(big)
    assert "截断" in out
