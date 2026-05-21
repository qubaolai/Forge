"""LocalTaskQueue 持久化存储 (SQLite, 短连接).

设计要点
--------
- **每次操作开/关连接**: ``async with aiosqlite.connect(path) as conn`` 在每个
  操作内独立开关. 这样 aiosqlite 后台线程会随上下文退出关闭, 不再因为残留连接
  把 event loop 卡死 (这是旧版用持久 ``self._conn`` 的根本问题).
  注意: 不要写成 ``async with await aiosqlite.connect(...)``: aiosqlite 的
  Connection 同时是 awaitable 和 async context manager, 先 ``await`` 已经把
  内部线程 start 过一次, 再走 ``__aenter__`` 会触发 ``threads can only be
  started once`` 异常.
- **SQLite WAL**: 任务表低频写入, 不开 WAL 性能也够; 简单优先.
- **状态机**: ``pending -> running -> succeeded`` 或 ``pending -> running ->
  pending (retry) -> ... -> dead``.

如果未来发现短连接性能瓶颈 (大量任务 / 秒), 再考虑回归持久连接 + 显式 lifecycle.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass
class LocalQueuedTask:
    id: int
    task_name: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


class LocalTaskStore:
    """本地任务状态存储 (SQLite, 短连接).

    状态机:
        pending -> running -> succeeded
                        |-> pending (retry)
                        |-> dead
    """

    _SCHEMA_INIT = """
        CREATE TABLE IF NOT EXISTS local_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            run_at REAL NOT NULL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """
    _SCHEMA_INDEX = (
        "CREATE INDEX IF NOT EXISTS idx_local_tasks_status_runat ON local_tasks(status, run_at, id)"
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(self._SCHEMA_INIT)
            await conn.execute(self._SCHEMA_INDEX)
            await conn.commit()

    async def aclose(self) -> None:
        """短连接模式无需关闭. 保留方法供调用方对齐生命周期."""
        return None

    async def enqueue(
        self,
        task_name: str,
        payload: dict[str, Any],
        *,
        max_attempts: int,
    ) -> int:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                INSERT INTO local_tasks(
                    task_name, payload_json, status, attempts, max_attempts,
                    run_at, created_at, updated_at
                ) VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (task_name, json.dumps(payload, ensure_ascii=False), max_attempts, now, now, now),
            )
            await conn.commit()
            return int(cur.lastrowid or 0)

    async def recover_running(self) -> int:
        """重启恢复: running -> pending."""
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                UPDATE local_tasks
                SET status='pending', run_at=?, updated_at=?
                WHERE status='running'
                """,
                (now, now),
            )
            await conn.commit()
            return int(cur.rowcount)

    async def claim_due(self) -> LocalQueuedTask | None:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    """
                    SELECT id, task_name, payload_json, attempts, max_attempts
                    FROM local_tasks
                    WHERE status='pending' AND run_at <= ?
                    ORDER BY id
                    LIMIT 1
                    """,
                    (now,),
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.commit()
                    return None
                await conn.execute(
                    "UPDATE local_tasks SET status='running', updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            payload = json.loads(str(row["payload_json"]))
            return LocalQueuedTask(
                id=int(row["id"]),
                task_name=str(row["task_name"]),
                payload=payload,
                attempts=int(row["attempts"]),
                max_attempts=int(row["max_attempts"]),
            )

    async def mark_succeeded(self, task_id: int) -> None:
        await self._update(
            "UPDATE local_tasks SET status='succeeded', updated_at=? WHERE id=?",
            (time.time(), task_id),
        )

    async def mark_retry(
        self,
        task_id: int,
        *,
        attempts: int,
        error: str,
        retry_delay_seconds: float,
    ) -> None:
        now = time.time()
        run_at = now + max(0.0, retry_delay_seconds)
        await self._update(
            """
            UPDATE local_tasks
            SET status='pending', attempts=?, last_error=?, run_at=?, updated_at=?
            WHERE id=?
            """,
            (attempts, error, run_at, now, task_id),
        )

    async def mark_dead(self, task_id: int, *, attempts: int, error: str) -> None:
        await self._update(
            """
            UPDATE local_tasks
            SET status='dead', attempts=?, last_error=?, updated_at=?
            WHERE id=?
            """,
            (attempts, error, time.time(), task_id),
        )

    async def next_due_delay(self) -> float | None:
        """返回下一条 pending 任务距离现在的等待秒数."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT run_at
                FROM local_tasks
                WHERE status='pending'
                ORDER BY run_at
                LIMIT 1
                """
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return max(0.0, float(row["run_at"]) - time.time())

    async def _update(self, sql: str, params: tuple[Any, ...]) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(sql, params)
            await conn.commit()

    # 测试辅助 — 业务不用
    async def fetch_all(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, task_name, status, attempts, last_error FROM local_tasks ORDER BY id"
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
