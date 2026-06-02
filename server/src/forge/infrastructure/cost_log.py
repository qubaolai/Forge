"""LLM 调用成本流水 (cost JSONL) — 取代 llm_cost_daily SQL 表.

对应方案文档:``单机化改造方案.md`` §5.3 + §6.3.

文件:``<global>/cost.jsonl``,单文件按调用顺序 append.

单机主场景下默认不依赖 ``user_id`` 维度;但为兼容迁移阶段的预算逻辑,
仍保留可选 ``user_id`` 字段. 同时保留 session/workflow/phase 维度,便于
P0-P6 后期按 workflow 聚合成本.

每行一条 ``CostEntry``:

    {"ts": "2026-05-20T10:32:20Z", "provider": "anthropic", "model": "sonnet-4-6",
     "prompt_tokens": 1234, "completion_tokens": 56, "cost_usd": 0.0089,
     "error": false, "session_id": "sess_xxx", "workflow_id": null, ...}

成本聚合 (按月 / 按 model / 按 workflow) 通过 ``aggregate(group_by, since)``
在调用方按需做; helper 不预聚合.

错误调用 (``error=True``) 仍写一行, ``cost_usd=0`` / token=0, 仅用于审计与
错误率统计.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from forge.config import paths
from forge.infrastructure.jsonl import JsonlLog
from forge.infrastructure.storage.data_protocols import CostStore

__all__ = ["CostEntry", "CostLog", "default_cost_log"]


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CostEntry:
    """单次 LLM 调用成本记录."""

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    error: bool = False
    user_id: str | None = None
    call_count: int | None = None
    error_count: int | None = None
    # 可选维度 (P0-P6 后期按 workflow 聚合用)
    session_id: str | None = None
    workflow_id: str | None = None
    phase_id: str | None = None
    agent_role: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CostEntry:
        kwargs = dict(d)
        ts = kwargs.get("ts")
        if isinstance(ts, str):
            kwargs["ts"] = datetime.fromisoformat(ts)
        # 兼容历史数据缺字段
        kwargs.setdefault("error", False)
        return cls(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# CostLog
# ─────────────────────────────────────────────────────────────────────────────


class CostLog(CostStore):
    """cost.jsonl 领域 wrapper.

    单进程内追加 + 按需读聚合. 写性能不是瓶颈 (LLM 调用频率 ~秒级);
    读做全文扫描, 假设单机一年累积 < 50MB.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.cost_log_path()
        self._log = JsonlLog(self.path)

    # ────────────────────── 写 ──────────────────────

    async def record(self, entry: CostEntry) -> None:
        await self._log.append(entry.to_dict())

    # ────────────────────── 读 / 聚合 ──────────────────────

    async def iter_all(self) -> AsyncIterator[CostEntry]:
        async for r in self._log.iter_all():
            yield CostEntry.from_dict(r)

    async def tail(self, n: int) -> list[CostEntry]:
        """末尾 n 条成本记录."""
        if n <= 0:
            return []
        records = [e async for e in self.iter_all()]
        return records[-n:]

    async def aggregate(
        self,
        group_by: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_errors: bool = False,
    ) -> dict[str, float]:
        """按 ``group_by`` 字段聚合 cost_usd, 返回 ``{group_value: total_usd}``.

        Args:
            group_by: 分组字段名 ('provider' / 'model' / 'agent_role' /
                      'workflow_id' / 'session_id' 等). 字段不存在时归到 ``"_"``.
            since: 起始时间 (含). None 表示不过滤.
            until: 截止时间 (含). None 表示不过滤.
            include_errors: 是否纳入 ``error=True`` 的调用 (默认不计, 成本为 0).
        """
        result: defaultdict[str, float] = defaultdict(float)
        async for e in self.iter_all():
            if not include_errors and e.error:
                continue
            if since is not None and e.ts < since:
                continue
            if until is not None and e.ts > until:
                continue
            key = getattr(e, group_by, None)
            result[str(key) if key is not None else "_"] += e.cost_usd
        return dict(result)

    async def total_usd(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> float:
        """指定时间窗口内的总成本 (USD)."""
        total = 0.0
        async for e in self.iter_all():
            if e.error:
                continue
            if since is not None and e.ts < since:
                continue
            if until is not None and e.ts > until:
                continue
            total += e.cost_usd
        return total

    async def today_total_usd(self, *, tz: Any = UTC) -> float:
        """今日 (UTC 0 点起算) 累计 USD.

        budget 检查的典型用法. 走单趟 ``iter_all``, 无内存累积状态,
        每次重新计算 — 单用户调用频率下没问题.
        """
        today_start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
        return await self.total_usd(since=today_start)


# ─────────────────────────────────────────────────────────────────────────────
# 默认实例 (按需在调用方持有, 这里只提供工厂)
# ─────────────────────────────────────────────────────────────────────────────


def default_cost_log() -> CostLog:
    """返回指向 ``<global>/cost.jsonl`` 的实例 (按需新建)."""
    return CostLog(paths.cost_log_path())
