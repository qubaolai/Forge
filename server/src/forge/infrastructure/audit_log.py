"""危险工具调用审计流水 (audit JSONL).

对应方案文档:``单机化改造方案.md`` §5.3 + §6.3 + P1 防护栏管线.

文件:``<global>/audit.jsonl``,单文件按调用顺序 append.

P1 完成后, ``ToolExecutor`` 在执行 ``dangerous=True`` 工具时 (write_file /
shell / git_ops_write / gh_ops / ...) 会写一行 ``AuditEntry``:

    {"ts": "2026-05-20T10:32:20Z", "tool": "shell", "outcome": "blocked",
     "args_summary": {"cmd": "rm -rf /"}, "reason": "blacklist_match",
     "workspace": "/Users/almond/code/ec", "session_id": "sess_xxx",
     "agent_role": "developer", "duration_ms": null}

记录维度
--------

``outcome`` 枚举 (str, 不强校验,保留扩展):

- ``"allowed"`` — 通过防护栏, 准备执行 (常规观察用, 可关)
- ``"executed"`` — 实际执行完成 (含 duration_ms)
- ``"blocked"`` — 被防护栏拦截 (黑名单 / scope / 权限 / 频率)
- ``"failed"`` — 执行中抛错 (reason = 异常类型)

``args_summary`` 由调用方按 ``tool.audit_payload_fields`` 白名单裁剪后传入,
helper 不做敏感字段过滤 (太长的 cmd / 大文本调用方自己截断).

查询
----

读写都是单趟全文扫描 (假设单机一年 < 50MB). 提供 ``filter`` 便捷查询:

    async for e in audit.filter(tool="shell", outcome="blocked"):
        ...

复杂多维聚合 (按天 / 按 workspace) 调用方自己跑 ``iter_all`` 累加.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import paths

from forge.infrastructure.jsonl import JsonlLog

__all__ = ["AuditEntry", "AuditLog", "default_audit_log"]


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AuditEntry:
    """单次危险工具调用审计记录."""

    tool: str
    outcome: str = "executed"  # allowed / executed / blocked / failed
    args_summary: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    duration_ms: float | None = None
    # 上下文维度 (可选)
    workspace: str | None = None
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
    def from_dict(cls, d: dict[str, Any]) -> AuditEntry:
        kwargs = dict(d)
        ts = kwargs.get("ts")
        if isinstance(ts, str):
            kwargs["ts"] = datetime.fromisoformat(ts)
        # 兼容历史数据缺字段
        kwargs.setdefault("outcome", "executed")
        kwargs.setdefault("args_summary", {})
        return cls(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog
# ─────────────────────────────────────────────────────────────────────────────


class AuditLog:
    """audit.jsonl 领域 wrapper.

    单进程内追加 + 按需读. 写性能不是瓶颈 (危险工具调用频率 << LLM 调用);
    读做全文扫描, 假设单机一年累积 < 50MB.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.audit_log_path()
        self._log = JsonlLog(self.path)

    # ────────────────────── 写 ──────────────────────

    async def record(self, entry: AuditEntry) -> None:
        await self._log.append(entry.to_dict())

    # ────────────────────── 读 / 查询 ──────────────────────

    async def iter_all(self) -> AsyncIterator[AuditEntry]:
        async for r in self._log.iter_all():
            yield AuditEntry.from_dict(r)

    async def tail(self, n: int) -> list[AuditEntry]:
        """末尾 n 条审计记录."""
        if n <= 0:
            return []
        records = [e async for e in self.iter_all()]
        return records[-n:]

    async def filter(
        self,
        *,
        tool: str | None = None,
        outcome: str | None = None,
        agent_role: str | None = None,
        workspace: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEntry]:
        """按维度过滤, 返回匹配的全部 entry (按时间顺序).

        所有参数 AND 关系; ``None`` 表示该维度不过滤.
        典型用法::

            blocked = await audit.filter(tool="shell", outcome="blocked")
            today = await audit.filter(since=datetime.combine(date.today(), time.min, tzinfo=UTC))
        """
        result: list[AuditEntry] = []
        async for e in self.iter_all():
            if tool is not None and e.tool != tool:
                continue
            if outcome is not None and e.outcome != outcome:
                continue
            if agent_role is not None and e.agent_role != agent_role:
                continue
            if workspace is not None and e.workspace != workspace:
                continue
            if session_id is not None and e.session_id != session_id:
                continue
            if since is not None and e.ts < since:
                continue
            if until is not None and e.ts > until:
                continue
            result.append(e)
        return result

    async def count(
        self,
        *,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """指定窗口 + outcome 内的审计记录数 (健康度信号).

        例如 ``await audit.count(outcome="blocked", since=today_start)`` 用于
        看板展示 "今日拦截 N 次危险调用".
        """
        total = 0
        async for e in self.iter_all():
            if outcome is not None and e.outcome != outcome:
                continue
            if since is not None and e.ts < since:
                continue
            if until is not None and e.ts > until:
                continue
            total += 1
        return total


# ─────────────────────────────────────────────────────────────────────────────
# 默认实例工厂
# ─────────────────────────────────────────────────────────────────────────────


def default_audit_log() -> AuditLog:
    """返回指向 ``<global>/audit.jsonl`` 的实例 (按需新建)."""
    return AuditLog(paths.audit_log_path())
