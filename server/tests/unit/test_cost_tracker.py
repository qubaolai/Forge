"""测试 LLM cost tracker (per-user + budget)."""

from __future__ import annotations

import logging

import pytest

from forge.core.request_context import user_id_scope
from forge.llm.cost_tracker import (
    BudgetConfig,
    CostTracker,
    LLMBudgetExceeded,
)


def _usage(p: int, c: int) -> dict:
    return {"prompt_tokens": p, "completion_tokens": c}


# ── 基础记账 ───────────────────────────────────────────────────────────────────
def test_record_success_with_usage():
    t = CostTracker()
    t.record("openai", "gpt-4o-mini", _usage(1000, 500))
    snap = t.snapshot()
    assert "anon:openai:gpt-4o-mini" in snap
    stat = snap["anon:openai:gpt-4o-mini"]
    assert stat["calls"] == 1
    assert stat["prompt_tokens"] == 1000
    assert stat["completion_tokens"] == 500
    assert stat["estimated_cost_usd"] > 0


def test_record_error():
    t = CostTracker()
    t.record("openai", "gpt-4o-mini", None, error=True)
    stat = t.snapshot()["anon:openai:gpt-4o-mini"]
    assert stat["calls"] == 1
    assert stat["errors"] == 1
    assert stat["prompt_tokens"] == 0


def test_accumulates_across_calls():
    t = CostTracker()
    t.record("openai", "gpt-4o-mini", _usage(100, 50))
    t.record("openai", "gpt-4o-mini", _usage(200, 100))
    stat = t.snapshot()["anon:openai:gpt-4o-mini"]
    assert stat["calls"] == 2
    assert stat["prompt_tokens"] == 300
    assert stat["completion_tokens"] == 150


def test_unknown_model_zero_cost():
    t = CostTracker()
    t.record("xxx", "unknown-model", _usage(1000, 500))
    stat = t.snapshot()["anon:xxx:unknown-model"]
    assert stat["estimated_cost_usd"] == 0.0
    assert stat["prompt_tokens"] == 1000


def test_supports_anthropic_field_names():
    t = CostTracker()
    t.record("anthropic", "claude-haiku-4-5", {"input_tokens": 100, "output_tokens": 50})
    stat = t.snapshot()["anon:anthropic:claude-haiku-4-5"]
    assert stat["prompt_tokens"] == 100
    assert stat["completion_tokens"] == 50
    assert stat["estimated_cost_usd"] > 0


def test_reset():
    t = CostTracker()
    t.record("openai", "gpt-4o-mini", _usage(100, 50))
    t.reset()
    assert t.snapshot() == {}


# ── per-user 维度 ─────────────────────────────────────────────────────────────
def test_per_user_dimension_via_explicit_arg():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1000, 500), user_id="u1")
    t.record("openai", "gpt-4o", _usage(2000, 100), user_id="u2")
    snap = t.snapshot()
    assert "u1:openai:gpt-4o" in snap
    assert "u2:openai:gpt-4o" in snap
    assert snap["u1:openai:gpt-4o"]["prompt_tokens"] == 1000
    assert snap["u2:openai:gpt-4o"]["prompt_tokens"] == 2000


def test_user_id_picked_from_context_var():
    t = CostTracker()
    with user_id_scope("ctx-user"):
        t.record("openai", "gpt-4o", _usage(100, 50))
    snap = t.snapshot()
    assert "ctx-user:openai:gpt-4o" in snap


def test_explicit_user_id_overrides_context_var():
    t = CostTracker()
    with user_id_scope("ctx-user"):
        t.record("openai", "gpt-4o", _usage(100, 50), user_id="explicit-user")
    snap = t.snapshot()
    assert "explicit-user:openai:gpt-4o" in snap
    assert "ctx-user:openai:gpt-4o" not in snap


def test_user_total_in_memory():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")
    # gpt-4o: 2.50 input + 10.00 output per 1M = 12.50 USD
    assert t.user_total_in_memory("u1") == pytest.approx(12.50, rel=1e-3)
    assert t.user_total_in_memory("u2") == 0


# ── BudgetConfig 检查 ─────────────────────────────────────────────────────────
def test_check_budget_no_op_when_no_limits():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(10_000_000, 10_000_000), user_id="u1")
    t.check_budget(user_id="u1")  # 默认 BudgetConfig 全 None, 不抛


def test_check_budget_raises_when_user_limit_exceeded():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")
    t.configure_budget(BudgetConfig(user_daily_limits_usd={"u1": 10.0}))
    with pytest.raises(LLMBudgetExceeded, match="u1"):
        t.check_budget(user_id="u1")
    # 其他 user 不受影响
    t.check_budget(user_id="u2")


def test_check_budget_uses_default_user_limit():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")
    t.configure_budget(BudgetConfig(default_user_daily_limit_usd=10.0))
    with pytest.raises(LLMBudgetExceeded):
        t.check_budget(user_id="u1")


def test_explicit_user_limit_overrides_default():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")
    t.configure_budget(
        BudgetConfig(
            user_daily_limits_usd={"u1": 100.0},
            default_user_daily_limit_usd=10.0,
        )
    )
    t.check_budget(user_id="u1")


def test_check_budget_raises_when_global_limit_exceeded():
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u2")
    # 总 25 USD > global 10
    t.configure_budget(BudgetConfig(global_daily_limit_usd=10.0))
    with pytest.raises(LLMBudgetExceeded, match="全局"):
        t.check_budget(user_id="u1")


def test_alert_threshold_warns_but_does_not_raise(caplog):
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(800_000, 0), user_id="u1")
    # 800k input * 2.50/1M = 2.0 USD; limit 2.5; ratio 0.8 ≥ 0.7
    t.configure_budget(
        BudgetConfig(
            user_daily_limits_usd={"u1": 2.5},
            alert_threshold=0.7,
        )
    )
    with caplog.at_level(logging.WARNING):
        t.check_budget(user_id="u1")
    assert any("接近上限" in r.message for r in caplog.records)


# ── daily_total_provider 注入 ─────────────────────────────────────────────────
def test_daily_total_provider_overrides_in_memory():
    """注入 daily provider (Phase 4b 用 DB), check_budget 应使用它."""
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(100, 100), user_id="u1")
    t.set_daily_total_provider(lambda uid: 100.0 if uid == "u1" else 0.0)
    t.configure_budget(BudgetConfig(user_daily_limits_usd={"u1": 50.0}))
    with pytest.raises(LLMBudgetExceeded):
        t.check_budget(user_id="u1")


def test_daily_total_provider_failure_falls_back_to_memory(caplog):
    t = CostTracker()
    t.record("openai", "gpt-4o", _usage(1_000_000, 1_000_000), user_id="u1")

    def bomb(_uid: str) -> float:
        raise RuntimeError("DB down")

    t.set_daily_total_provider(bomb)
    t.configure_budget(BudgetConfig(user_daily_limits_usd={"u1": 10.0}))
    with caplog.at_level(logging.ERROR), pytest.raises(LLMBudgetExceeded):
        t.check_budget(user_id="u1")
    assert any("daily_total_provider" in r.message for r in caplog.records)
