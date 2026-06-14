"""ToolResultPolicy 单测."""

from __future__ import annotations

from forge.context_mgmt.meter.token_meter import DefaultTokenMeter
from forge.context_mgmt.tool_policy.truncating import TruncatingPolicy
from forge.llm.token_counter import HeuristicCounter


def _meter():
    return DefaultTokenMeter(HeuristicCounter())


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
