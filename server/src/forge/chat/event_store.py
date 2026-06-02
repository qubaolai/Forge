"""Chat 事件流持久化 (per assistant message_id).

设计:
    - 每条 assistant 消息一个独立目录: <chat_runs_dir>/<message_id>/
        - state.json   : turn 元数据 + 终态
        - events.jsonl : 所有 SSE 事件 (fsync=True), 一行一事件, 带 seq
    - seq 单调递增, 从 1 开始, 启动时从已有文件扫描重建 (resume 场景)
    - 任何 emit 落盘失败应让上层任务进入 error 终态, 不静默吞

写入路径优先级 (最重要):
    1. events.jsonl  -- source of truth, fsync 持久化
    2. broadcaster   -- 内存推送给实时订阅者 (掉了不影响数据)
    3. DB message    -- 最终态由 finalizer 折叠 events 写入

读取场景:
    - 实时订阅: 先 iter_events(after_seq=last_seq) 回放历史, 再接 broadcaster
    - resume:   折叠 events.jsonl 重建 prev_content / prev_tool_calls
    - 启动恢复: 扫描所有 state.status=='running' 的目录 (本期不做, 但 schema 留好)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

from forge.config import paths
from forge.infrastructure.jsonl import JsonlLog, atomic_write_text

logger = logging.getLogger(__name__)


class ChatEventStore:
    """单条 assistant message 的事件存储 + 状态快照.

    线程安全: 单进程 asyncio 单线程; seq 自增用 asyncio.Lock 保证不重复.
    跨进程: 假设单 assistant 进程 (与项目其它存储一致); 多进程需要 filelock.
    """

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        self._dir = paths.chat_run_dir(message_id)
        self._state_path = paths.chat_run_state_path(message_id)
        self._events_log = JsonlLog(paths.chat_run_events_path(message_id), fsync=True)
        self._seq = 0
        self._seq_lock = asyncio.Lock()
        self._seq_initialized = False

    # ------------------------------------------------------------------
    # seq 管理
    # ------------------------------------------------------------------
    async def initialize_seq(self) -> int:
        """扫描 events.jsonl 找到当前最大 seq.

        新 turn: 文件不存在, seq=0 -> 第一次 append 得到 seq=1.
        续写: 已有 N 条事件, seq=N -> 下次 append 得到 seq=N+1.
        """
        if self._seq_initialized:
            return self._seq
        max_seq = 0
        async for record in self._events_log.iter_all():
            try:
                s = int(record.get("seq", 0))
                if s > max_seq:
                    max_seq = s
            except (TypeError, ValueError):
                continue
        self._seq = max_seq
        self._seq_initialized = True
        return self._seq

    # ------------------------------------------------------------------
    # 事件读写
    # ------------------------------------------------------------------
    async def append_event(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """追加事件 (fsync 持久化), 返回带 seq + ts 的完整记录.

        event_dict 形如: {"type": "delta", "content": "..."}
        返回:           {"seq": 5, "ts": "...", "type": "delta", "content": "..."}
        """
        if not self._seq_initialized:
            await self.initialize_seq()
        async with self._seq_lock:
            self._seq += 1
            seq = self._seq
        record: dict[str, Any] = {
            "seq": seq,
            "ts": datetime.now(UTC).isoformat(),
            **event_dict,
        }
        await self._events_log.append(record)
        return record

    async def iter_events(self, *, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        """按 seq 升序 yield 事件 (seq > after_seq 的部分).

        用于:
            - 订阅者首次连接: 回放全部历史 (after_seq=0)
            - 断线重连: 从客户端记录的 last_seq 之后继续
            - resume: 折叠重建 prev_content
        """
        async for record in self._events_log.iter_all():
            try:
                s = int(record.get("seq", 0))
            except (TypeError, ValueError):
                continue
            if s > after_seq:
                yield record

    # ------------------------------------------------------------------
    # state.json
    # ------------------------------------------------------------------
    async def save_state(self, state: dict[str, Any]) -> None:
        """原子写 state.json (整体覆盖)."""
        state = dict(state)
        state.setdefault("message_id", self.message_id)
        state["updated_at"] = datetime.now(UTC).isoformat()
        content = (
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        await atomic_write_text(self._state_path, content)

    async def load_state(self) -> dict[str, Any] | None:
        """读 state.json. 不存在返回 None."""
        if not self._state_path.exists():
            return None
        try:
            async with aiofiles.open(self._state_path, encoding="utf-8") as f:
                raw = await f.read()
            return json.loads(raw or "null")
        except (json.JSONDecodeError, OSError):
            logger.exception("state.json 解析失败 message_id=%s", self.message_id)
            return None

    async def update_state(self, **patches: Any) -> dict[str, Any]:
        """读-改-写 state.json. 返回更新后的完整 state."""
        existing = await self.load_state() or {"message_id": self.message_id}
        existing.update(patches)
        await self.save_state(existing)
        return existing

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def current_seq(self) -> int:
        """当前已知最大 seq (须先 initialize_seq 才反映磁盘历史)."""
        return self._seq


__all__ = ["ChatEventStore"]
