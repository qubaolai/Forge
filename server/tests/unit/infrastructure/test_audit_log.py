"""AuditLog 单元测试.

覆盖:
- record + iter_all 往返 (含 datetime + args_summary)
- tail 边界
- filter by tool / outcome / agent_role / workspace / session_id
- filter 多维 AND
- filter 时间窗口 since / until
- count by outcome + 时间窗口
- 空文件 / 缺字段兼容 (outcome 默认 executed; args_summary 默认 {})
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge.infrastructure.audit_log import AuditEntry, AuditLog

pytestmark = pytest.mark.asyncio


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


# ────────────────────── 基本 record / iter ──────────────────────


async def test_record_and_iter_all(log: AuditLog) -> None:
    e = AuditEntry(
        tool="shell",
        outcome="executed",
        args_summary={"cmd": "ls -la"},
        duration_ms=42.3,
        workspace="/Users/almond/code/ec",
        session_id="sess_x",
        agent_role="developer",
    )
    await log.record(e)

    records = [r async for r in log.iter_all()]
    assert len(records) == 1
    got = records[0]
    assert got.tool == "shell"
    assert got.outcome == "executed"
    assert got.args_summary == {"cmd": "ls -la"}
    assert got.duration_ms == pytest.approx(42.3)
    assert got.workspace == "/Users/almond/code/ec"
    assert got.session_id == "sess_x"
    assert got.agent_role == "developer"


async def test_datetime_roundtrip(log: AuditLog) -> None:
    ts = datetime(2026, 5, 20, 10, 32, 20, tzinfo=UTC)
    await log.record(AuditEntry(tool="write_file", ts=ts))

    [r] = [x async for x in log.iter_all()]
    assert r.ts == ts


async def test_record_blocked_with_reason(log: AuditLog) -> None:
    """被拦截的危险调用应保留 reason."""
    await log.record(
        AuditEntry(
            tool="shell",
            outcome="blocked",
            args_summary={"cmd": "rm -rf /"},
            reason="blacklist_match",
        )
    )
    [r] = [x async for x in log.iter_all()]
    assert r.outcome == "blocked"
    assert r.reason == "blacklist_match"
    assert r.duration_ms is None


# ────────────────────── tail ──────────────────────


async def test_tail(log: AuditLog) -> None:
    for i in range(10):
        await log.record(AuditEntry(tool=f"t_{i}"))
    last3 = await log.tail(3)
    assert [r.tool for r in last3] == ["t_7", "t_8", "t_9"]


async def test_tail_zero_or_negative(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="t"))
    assert await log.tail(0) == []
    assert await log.tail(-1) == []


# ────────────────────── filter 单维 ──────────────────────


async def test_filter_by_tool(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="shell"))
    await log.record(AuditEntry(tool="write_file"))
    await log.record(AuditEntry(tool="shell"))

    result = await log.filter(tool="shell")
    assert [r.tool for r in result] == ["shell", "shell"]


async def test_filter_by_outcome(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="shell", outcome="executed"))
    await log.record(AuditEntry(tool="shell", outcome="blocked"))
    await log.record(AuditEntry(tool="shell", outcome="failed"))

    blocked = await log.filter(outcome="blocked")
    assert len(blocked) == 1
    assert blocked[0].outcome == "blocked"


async def test_filter_by_agent_role(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="shell", agent_role="developer"))
    await log.record(AuditEntry(tool="shell", agent_role="qa"))
    await log.record(AuditEntry(tool="shell", agent_role="developer"))

    devs = await log.filter(agent_role="developer")
    assert len(devs) == 2


async def test_filter_by_workspace(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="t", workspace="/a"))
    await log.record(AuditEntry(tool="t", workspace="/b"))
    await log.record(AuditEntry(tool="t", workspace="/a"))

    result = await log.filter(workspace="/a")
    assert len(result) == 2


async def test_filter_by_session_id(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="t", session_id="sess_1"))
    await log.record(AuditEntry(tool="t", session_id="sess_2"))

    result = await log.filter(session_id="sess_1")
    assert len(result) == 1
    assert result[0].session_id == "sess_1"


# ────────────────────── filter 多维 AND ──────────────────────


async def test_filter_multiple_conditions_and(log: AuditLog) -> None:
    """多个过滤条件按 AND 关系组合."""
    await log.record(AuditEntry(tool="shell", outcome="blocked", agent_role="developer"))
    await log.record(AuditEntry(tool="shell", outcome="blocked", agent_role="qa"))
    await log.record(AuditEntry(tool="shell", outcome="executed", agent_role="developer"))
    await log.record(AuditEntry(tool="write_file", outcome="blocked", agent_role="developer"))

    result = await log.filter(tool="shell", outcome="blocked", agent_role="developer")
    assert len(result) == 1


# ────────────────────── filter 时间窗口 ──────────────────────


async def test_filter_since(log: AuditLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(AuditEntry(tool="t", ts=base))
    await log.record(AuditEntry(tool="t", ts=base + timedelta(hours=1)))
    await log.record(AuditEntry(tool="t", ts=base + timedelta(days=2)))

    # since 半小时后 → 取后两条
    result = await log.filter(since=base + timedelta(minutes=30))
    assert len(result) == 2


async def test_filter_until(log: AuditLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(AuditEntry(tool="t", ts=base))
    await log.record(AuditEntry(tool="t", ts=base + timedelta(days=1)))

    result = await log.filter(until=base + timedelta(hours=1))
    assert len(result) == 1


async def test_filter_since_and_until_window(log: AuditLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(AuditEntry(tool="t", ts=base))
    await log.record(AuditEntry(tool="t", ts=base + timedelta(hours=2)))
    await log.record(AuditEntry(tool="t", ts=base + timedelta(hours=5)))

    result = await log.filter(since=base + timedelta(hours=1), until=base + timedelta(hours=3))
    assert len(result) == 1


# ────────────────────── count ──────────────────────


async def test_count_by_outcome(log: AuditLog) -> None:
    await log.record(AuditEntry(tool="t", outcome="executed"))
    await log.record(AuditEntry(tool="t", outcome="blocked"))
    await log.record(AuditEntry(tool="t", outcome="blocked"))
    await log.record(AuditEntry(tool="t", outcome="failed"))

    assert await log.count(outcome="blocked") == 2
    assert await log.count(outcome="executed") == 1


async def test_count_in_time_window(log: AuditLog) -> None:
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await log.record(AuditEntry(tool="t", outcome="blocked", ts=base))
    await log.record(AuditEntry(tool="t", outcome="blocked", ts=base + timedelta(hours=2)))
    await log.record(AuditEntry(tool="t", outcome="executed", ts=base + timedelta(hours=2)))

    cnt = await log.count(outcome="blocked", since=base + timedelta(hours=1))
    assert cnt == 1


async def test_count_no_filter_total(log: AuditLog) -> None:
    for _ in range(5):
        await log.record(AuditEntry(tool="t"))
    assert await log.count() == 5


# ────────────────────── 空 / 缺字段兼容 ──────────────────────


async def test_iter_all_empty_file(log: AuditLog) -> None:
    records = [r async for r in log.iter_all()]
    assert records == []


async def test_filter_empty_returns_empty_list(log: AuditLog) -> None:
    assert await log.filter(tool="shell") == []


async def test_count_empty_returns_zero(log: AuditLog) -> None:
    assert await log.count() == 0


async def test_missing_outcome_field_defaults_to_executed(log: AuditLog) -> None:
    """老数据若没 outcome 字段, 默认按 executed 处理."""
    # 直接写一条没 outcome 的旧数据 (绕过 AuditEntry)
    from forge.infrastructure.jsonl import JsonlLog

    raw = JsonlLog(log.path)
    await raw.append(
        {
            "tool": "shell",
            "args_summary": {"cmd": "ls"},
            "ts": "2026-05-20T10:00:00+00:00",
        }
    )

    [r] = [x async for x in log.iter_all()]
    assert r.outcome == "executed"
    assert r.args_summary == {"cmd": "ls"}


async def test_missing_args_summary_field_defaults_to_empty_dict(log: AuditLog) -> None:
    """老数据若没 args_summary 字段, 默认空 dict."""
    from forge.infrastructure.jsonl import JsonlLog

    raw = JsonlLog(log.path)
    await raw.append(
        {
            "tool": "shell",
            "outcome": "blocked",
            "ts": "2026-05-20T10:00:00+00:00",
        }
    )

    [r] = [x async for x in log.iter_all()]
    assert r.args_summary == {}
    assert r.outcome == "blocked"
