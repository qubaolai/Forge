from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge.config.domains.quota import UsageQuotaWindowSettings, UserQuotaSettings

from forge.infrastructure.cost_log import CostEntry, default_cost_log
from forge.quota import UsageQuotaManager, UserQuotaExceeded


def _settings() -> UserQuotaSettings:
    return UserQuotaSettings(
        enabled=True,
        five_hour=UsageQuotaWindowSettings(
            window_seconds=5 * 60 * 60,
            limit_usd=1.0,
            limit_tokens=10_000,
            limit_calls=10,
        ),
        weekly=UsageQuotaWindowSettings(
            window_seconds=7 * 24 * 60 * 60,
            limit_usd=3.0,
            limit_tokens=30_000,
            limit_calls=30,
        ),
    )


def test_blocks_on_rolling_five_hour_cost_limit() -> None:
    quota = UsageQuotaManager()
    quota.configure(_settings())
    quota.record(user_id="u1", cost_usd=1.0, tokens=100, calls=1)

    with pytest.raises(UserQuotaExceeded, match="five_hour"):
        quota.check_sync("u1")

    quota.check_sync("u2")


def test_mark_persisted_removes_memory_events() -> None:
    quota = UsageQuotaManager()
    quota.configure(_settings())
    cutoff = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    quota.record(user_id="u1", cost_usd=1.0, tokens=100, ts=cutoff)

    quota.mark_persisted(cutoff)
    quota.check_sync("u1")


@pytest.mark.asyncio
async def test_status_reads_cost_log_and_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    now = datetime.now(UTC)
    await default_cost_log().record(
        CostEntry(
            provider="openai",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.4,
            user_id="u1",
            call_count=2,
            ts=now - timedelta(minutes=10),
        )
    )

    quota = UsageQuotaManager()
    quota.configure(_settings())
    quota.record(user_id="u1", cost_usd=0.1, tokens=25, calls=1)

    status = await quota.status("u1")
    five_hour = next(w for w in status.windows if w.name == "five_hour")

    assert five_hour.used_usd == pytest.approx(0.5)
    assert five_hour.used_tokens == 175
    assert five_hour.used_calls == 3


def test_disabled_quota_is_noop() -> None:
    quota = UsageQuotaManager()
    quota.configure(UserQuotaSettings(enabled=False))
    quota.record(user_id="u1", cost_usd=999, tokens=999, calls=999)

    quota.check_sync("u1")
