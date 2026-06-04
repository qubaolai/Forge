"""测试 CostTracker.flush_to_db / hydrate_baseline 与 CostLog 的交互."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge.llm.cost_tracker import CostTracker


@pytest.mark.asyncio
async def test_flush_empty_stats_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    t = CostTracker()
    written = await t.flush_to_db()
    assert written == 0


@pytest.mark.asyncio
async def test_flush_writes_costlog_and_clears_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    t = CostTracker()
    t.record("openai", "gpt-4o", {"prompt_tokens": 100, "completion_tokens": 50}, user_id="u1")
    t.record("anthropic", "claude-opus-4", {"input_tokens": 30, "output_tokens": 10}, user_id="u1")
    t.record("openai", "gpt-4o", {"prompt_tokens": 5, "completion_tokens": 5}, user_id="u2")

    written = await t.flush_to_db()
    assert written == 3
    assert t.snapshot() == {}
    assert t._db_baseline["u1"] > 0
    assert t._db_baseline["u2"] > 0


@pytest.mark.asyncio
async def test_hydrate_baseline_from_existing_costlog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    t1 = CostTracker()
    t1.record(
        "openai", "gpt-4o", {"prompt_tokens": 1_000_000, "completion_tokens": 0}, user_id="u1"
    )
    await t1.flush_to_db()

    t2 = CostTracker()
    await t2.hydrate_baseline()
    assert t2._db_baseline["u1"] == pytest.approx(2.5, rel=1e-3)


@pytest.mark.asyncio
async def test_hydrate_baseline_ignores_old_days(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))

    # 手工写一条昨日记录, hydrate 不应算入当日 baseline.
    from forge.infrastructure.cost_log import CostEntry, default_cost_log

    log = default_cost_log()
    yesterday = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=1
    )
    await log.record(
        CostEntry(
            provider="openai",
            model="gpt-4o",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cost_usd=2.5,
            user_id="u1",
            ts=yesterday,
        )
    )

    t = CostTracker()
    await t.hydrate_baseline()
    assert t._db_baseline.get("u1", 0.0) == 0.0


@pytest.mark.asyncio
async def test_user_total_uses_baseline_plus_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    t = CostTracker()
    t._db_baseline["u1"] = 5.0
    # gpt-4o: 1M input + 1M output => 12.5 USD
    t.record(
        "openai",
        "gpt-4o",
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        user_id="u1",
    )
    total = t._user_total("u1")
    assert total == pytest.approx(17.5, rel=1e-3)
