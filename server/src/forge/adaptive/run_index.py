"""全局 AdaptiveRun 索引 (C11/D6).

为什么需要：
- AdaptiveRunStore 按 workspace_path 隔离存储；查询 API 不传 workspace_path
  默认会落到 ``Path.cwd()``，多 workspace 时直接查不到 run。
- 引入轻量级全局索引：在 ``<app_data>/adaptive_runs_index.jsonl`` 记录每个 run
  对应的 ``(run_id, owner_user_id, workspace_path, created_at)``，让查询 API
  无需客户端持续维护 workspace_path 上下文。
- 索引文件是 append-only 的 jsonl，崩溃容忍度高。lookup 走 ``iter_all``
  反向扫描，找到最新一条即返回。

写时机：
- ``runs.create_run`` 与 ``chat.adaptive`` 创建 run 后调用 ``record_run()``。
- ``supervisor.start_run`` 与 abort/decide 路径不重复写——保持 index 是 "建档清单"，
  而非完整生命周期记录。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from forge.config import paths

from forge.infrastructure.jsonl import JsonlLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunIndexEntry:
    run_id: str
    owner_user_id: str
    workspace_path: str
    created_at: str  # iso8601


def _index_path() -> Path:
    return paths.app_data_dir() / "adaptive_runs_index.jsonl"


async def record_run(*, run_id: str, owner_user_id: str, workspace_path: str) -> None:
    """追加一条 run 建档记录。"""
    log = JsonlLog(_index_path(), fsync=True)
    await log.append(
        {
            "run_id": run_id,
            "owner_user_id": owner_user_id,
            "workspace_path": workspace_path,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


async def lookup(run_id: str) -> RunIndexEntry | None:
    """按 run_id 反查全局索引；不存在返回 None。"""
    log = JsonlLog(_index_path(), fsync=False)
    # 简化实现：全文扫一遍取最后一条匹配项；index 量级 <= 万行，性能可接受
    last: RunIndexEntry | None = None
    async for row in log.iter_all():
        if str(row.get("run_id", "")) == run_id:
            last = RunIndexEntry(
                run_id=run_id,
                owner_user_id=str(row.get("owner_user_id", "")),
                workspace_path=str(row.get("workspace_path", "")),
                created_at=str(row.get("created_at", "")),
            )
    return last


async def list_by_owner(owner_user_id: str | None = None) -> list[RunIndexEntry]:
    """列出索引中的所有 run，可按 owner 过滤。倒序（最新在前）。"""
    log = JsonlLog(_index_path(), fsync=False)
    entries: list[RunIndexEntry] = []
    async for row in log.iter_all():
        if owner_user_id is not None and str(row.get("owner_user_id", "")) != owner_user_id:
            continue
        entries.append(
            RunIndexEntry(
                run_id=str(row.get("run_id", "")),
                owner_user_id=str(row.get("owner_user_id", "")),
                workspace_path=str(row.get("workspace_path", "")),
                created_at=str(row.get("created_at", "")),
            )
        )
    entries.reverse()
    return entries
