"""CostLog 单元测试.

覆盖:
- record + iter_all 往返 (含 datetime)
- tail 边界
- aggregate by provider / model / agent_role
- aggregate since/until 时间窗口
- error=True 默认不计成本
- include_errors 开关
- today_total_usd
- 空文件 / 缺字段兼容
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge.infrastructure.cost_log import CostEntry, CostLog

pytestmark = pytest.mark.asyncio


@pytest.fixture
def log(tmp_path: Path) -> CostLog:
    return CostLog(tmp_path / "cost.jsonl")


# ────────────────────── 基本 record / iter ──────────────────────


async def test_record_and_iter_all(log: CostLog) -> None:
    e = CostEntry(
        provider="anthropic",
        model="sonnet-4-6",
        prompt_tokens=1234,
        completion_tokens=56,
        cost_usd=0.0089,
        session_id="sess_x",
    )
    await log.record(e)

    records = [r async for r in log.iter_all()]
    assert len(records) == 1
    got = records[0]
    assert got.provider == "anthropic"
    assert got.model == "sonnet-4-6"
    assert got.cost_usd == pytest.approx(0.0089)
    assert got.session_id == "sess_x"


async def test_datetime_roundtrip(log: CostLog) -> None:
    ts = datetime(2026, 5, 20, 10, 32, 20, tzinfo=UTC)
    await log.record(CostEntry(provider="p", model="m", ts=ts))

    [r] = [x async for x in log.iter_all()]
    assert r.ts == ts


# ────────────────────── tail ──────────────────────


async def test_tail(log: CostLog) -> None:
    for i in range(10):
        await log.record(CostEntry(provider="p", model=f"m_{i}", cost_usd=i * 1.0))
    last3 = await log.tail(3)
    assert [r.model for r in last3] == ["m_7", "m_8", "m_9"]


async def test_tail_zero_or_negative(log: CostLog) -> None:
    await log.record(CostEntry(provider="p", model="m"))
    assert await log.tail(0) == []
    assert await log.tail(-1) == []


# ────────────────────── aggregate ──────────────────────


async def test_aggregate_by_model(log: CostLog) -> None:
    await log.record(CostEntry(provider="openai", model="gpt-4o", cost_usd=1.0))
    await log.record(CostEntry(provider="openai", model="gpt-4o", cost_usd=2.5))
    await log.record(CostEntry(provider="openai", model="gpt-4o-mini", cost_usd=0.5))

    result = await log.aggregate("model")
    assert result == {"gpt-4o": pytest.approx(3.5), "gpt-4o-mini": pytest.approx(0.5)}


async def test_aggregate_by_provider(log: CostLog) -> None:
    await log.record(CostEntry(provider="openai", model="x", cost_usd=1.0))
    await log.record(CostEntry(provider="anthropic", model="y", cost_usd=2.0))
    await log.record(CostEntry(provider="openai", model="z", cost_usd=3.0))

    result = await log.aggregate("provider")
    assert result == {"openai": pytest.approx(4.0), "anthropic": pytest.approx(2.0)}


async def test_aggregate_by_optional_field_uses_underscore_for_none(
    log: CostLog,
) -> None:
    """workflow_id 为 None 的条目归到 ``_`` bucket."""
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0, workflow_id="wf_1"))
    await log.record(CostEntry(provider="p", model="m", cost_usd=2.0, workflow_id=None))
    await log.record(CostEntry(provider="p", model="m", cost_usd=3.0, workflow_id="wf_1"))

    result = await log.aggregate("workflow_id")
    assert result == {"wf_1": pytest.approx(4.0), "_": pytest.approx(2.0)}


async def test_aggregate_since_filter(log: CostLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0, ts=base))
    await log.record(CostEntry(provider="p", model="m", cost_usd=2.0, ts=base + timedelta(hours=1)))
    await log.record(CostEntry(provider="p", model="m", cost_usd=4.0, ts=base + timedelta(days=2)))

    # since 半小时后 -> 取后两条
    result = await log.aggregate("model", since=base + timedelta(minutes=30))
    assert result == {"m": pytest.approx(6.0)}


async def test_aggregate_until_filter(log: CostLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0, ts=base))
    await log.record(CostEntry(provider="p", model="m", cost_usd=2.0, ts=base + timedelta(days=1)))

    result = await log.aggregate("model", until=base + timedelta(hours=1))
    assert result == {"m": pytest.approx(1.0)}


# ────────────────────── errors ──────────────────────


async def test_error_entries_excluded_by_default(log: CostLog) -> None:
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0))
    await log.record(CostEntry(provider="p", model="m", cost_usd=0.0, error=True))

    result = await log.aggregate("model")
    assert result == {"m": pytest.approx(1.0)}


async def test_aggregate_include_errors(log: CostLog) -> None:
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0))
    await log.record(CostEntry(provider="p", model="m", cost_usd=0.5, error=True))

    result = await log.aggregate("model", include_errors=True)
    assert result == {"m": pytest.approx(1.5)}


# ────────────────────── total / today ──────────────────────


async def test_total_usd(log: CostLog) -> None:
    for cost in [0.1, 0.2, 0.3]:
        await log.record(CostEntry(provider="p", model="m", cost_usd=cost))
    assert await log.total_usd() == pytest.approx(0.6)


async def test_total_usd_skips_errors(log: CostLog) -> None:
    await log.record(CostEntry(provider="p", model="m", cost_usd=1.0))
    await log.record(CostEntry(provider="p", model="m", cost_usd=5.0, error=True))
    assert await log.total_usd() == pytest.approx(1.0)


async def test_today_total_usd(log: CostLog) -> None:
    """今日记录算进, 昨日不算."""
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await log.record(CostEntry(provider="p", model="m", cost_usd=0.5, ts=yesterday))
    await log.record(CostEntry(provider="p", model="m", cost_usd=0.7, ts=now))

    total = await log.today_total_usd()
    assert total == pytest.approx(0.7)


# ────────────────────── 空 / 损坏 ──────────────────────


async def test_aggregate_empty_returns_empty_dict(log: CostLog) -> None:
    result = await log.aggregate("model")
    assert result == {}


async def test_total_usd_empty_returns_zero(log: CostLog) -> None:
    assert await log.total_usd() == 0.0


async def test_missing_error_field_treated_as_false(log: CostLog) -> None:
    """老数据若没 error 字段, 默认按非错误处理."""
    # 直接写一条没 error 的旧数据 (绕过 CostEntry)
    from forge.infrastructure.jsonl import JsonlLog

    raw = JsonlLog(log.path)
    await raw.append(
        {
            "provider": "p",
            "model": "m",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 1.23,
            "ts": "2026-05-20T10:00:00+00:00",
        }
    )

    result = await log.aggregate("model")
    assert result == {"m": pytest.approx(1.23)}
