"""ChatTurnSupervisor: 全局 chat turn 注册表 + 后台清理.

职责:
    1. 注册 / 查找 / 中止活跃 TurnRun (按 message_id 索引)
    2. 终止 TurnRun 内存清理: 完成后保留 N 分钟以应付前端重连
    3. 磁盘清理: 完成超过 7 天的 events.jsonl 目录直接删除
    4. 进程关停时取消所有未完成 TurnRun (events.jsonl 已 fsync, 数据不丢)

线程模型:
    asyncio 单线程 + 一把 asyncio.Lock 保证 register / abort / get 并发安全.

进程关停语义:
    cancel 所有未完成 task → 各自 _wrap_execution 在 except CancelledError 里把
    DB 状态标 aborted、events.jsonl 已 fsync. 不再做异步 await DB 操作 (可能挂).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from forge.chat.turn_run import ChatTurnRun
from forge.config import paths

logger = logging.getLogger(__name__)

# 默认清理参数 (lifespan 会显式传入, 这里只做后备)
DEFAULT_EVICT_AFTER_SECONDS = 600       # 终态 turn 在内存里留 10 分钟应付重连
DEFAULT_RETENTION_DAYS = 7              # events.jsonl 目录保留 7 天
DEFAULT_CLEANUP_INTERVAL_SECONDS = 3600  # 每小时跑一次清理扫描


class ChatTurnSupervisor:
    """进程内 chat turn 注册表."""

    def __init__(
        self,
        *,
        evict_after_seconds: int = DEFAULT_EVICT_AFTER_SECONDS,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        self._runs: dict[str, ChatTurnRun] = {}
        self._lock = asyncio.Lock()
        self._evict_after = evict_after_seconds
        self._retention_days = retention_days
        self._cleanup_interval = cleanup_interval_seconds
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 注册 / 查询 / 中止
    # ------------------------------------------------------------------
    async def register(self, run: ChatTurnRun) -> None:
        """注册新 TurnRun. 同 message_id 已有活跃 run 时拒绝 (并发 resume 防护)."""
        async with self._lock:
            existing = self._runs.get(run.message_id)
            if existing is not None and not existing.is_terminal:
                raise RuntimeError(
                    f"message_id={run.message_id} 已有活跃 turn, 不能重复启动",
                )
            self._runs[run.message_id] = run

    def get(self, message_id: str) -> ChatTurnRun | None:
        """查找指定 message_id 的 TurnRun (活跃或保留中)."""
        return self._runs.get(message_id)

    def has_active(self, message_id: str) -> bool:
        """是否有正在跑的 TurnRun (非终态)."""
        run = self._runs.get(message_id)
        return run is not None and not run.is_terminal

    def abort(self, message_id: str) -> bool:
        """signal 指定 turn 停止. 返回 True 表示找到了 (不论是否已终态)."""
        run = self._runs.get(message_id)
        if run is None:
            return False
        if run.is_terminal:
            return True  # 已经结束, 无需 set 但视为找到
        run.abort()
        return True

    def list_active(self) -> list[ChatTurnRun]:
        """返回所有未终态的 run (debug / metrics 用)."""
        return [r for r in self._runs.values() if not r.is_terminal]

    # ------------------------------------------------------------------
    # 后台清理
    # ------------------------------------------------------------------
    def start_cleanup_loop(self) -> None:
        """启动周期性清理任务 (lifespan startup 时调一次)."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="chat-supervisor-cleanup",
        )

    async def _cleanup_loop(self) -> None:
        """周期 evict 内存 + 清磁盘. 任何异常不退出循环."""
        # 启动后等一会再跑第一次, 避免与启动其它流程争资源
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        while True:
            try:
                self.evict_terminated()
                await self.cleanup_disk()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("chat supervisor 清理循环异常, 已忽略")
            try:
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                return

    def evict_terminated(self) -> int:
        """内存 evict: 终态超过 evict_after 的 run 从注册表里删."""
        now = datetime.now(UTC)
        evicted = 0
        for mid, run in list(self._runs.items()):
            if not run.is_terminal or run.finished_at is None:
                continue
            age = (now - run.finished_at).total_seconds()
            if age >= self._evict_after:
                self._runs.pop(mid, None)
                evicted += 1
        if evicted:
            logger.info("evict 内存 chat turn %d 条 (alive=%d)", evicted, len(self._runs))
        return evicted

    async def cleanup_disk(self) -> int:
        """磁盘清理: 完成超过 retention_days 的目录 rm -rf."""
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        root = paths.chat_runs_dir()
        if not root.exists():
            return 0
        removed = 0
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if self._should_remove(entry, cutoff):
                try:
                    shutil.rmtree(entry)
                    removed += 1
                except Exception:  # noqa: BLE001
                    logger.exception("清理 chat_runs 目录失败 %s", entry)
        if removed:
            logger.info("磁盘清理 chat_runs 目录 %d 个 (retention=%d 天)",
                        removed, self._retention_days)
        return removed

    @staticmethod
    def _should_remove(entry: Path, cutoff: datetime) -> bool:
        """目录里 state.json.finished_at 早于 cutoff → 可清.

        无 state.json / 解析失败 / status 不是终态 → 不清 (保守).
        """
        state_path = entry / "state.json"
        if not state_path.exists():
            return False
        try:
            import json as _json
            payload = _json.loads(state_path.read_text(encoding="utf-8") or "null")
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        finished_at = payload.get("finished_at")
        if not finished_at:
            return False
        try:
            ts = datetime.fromisoformat(finished_at)
        except (TypeError, ValueError):
            return False
        return ts < cutoff

    # ------------------------------------------------------------------
    # 关停
    # ------------------------------------------------------------------
    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """进程关停: 停清理任务 + 取消所有未完成 TurnRun.

        每个 TurnRun 的 _wrap_execution finally 会写 state.json + DB.
        timeout 控制等待背景 task 收尾的总时长.
        """
        # 1. 停清理任务
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._cleanup_task
            self._cleanup_task = None

        # 2. 取消所有未完成 turn
        active = [r for r in self._runs.values() if not r.is_terminal]
        if not active:
            return
        logger.info("supervisor shutdown: 取消未完成 turn %d 个", len(active))
        for run in active:
            run.abort()  # 优雅 abort, 让 agent 自己 break + finalizer 落库

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(r.wait_done() for r in active),
                    return_exceptions=True,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("supervisor shutdown 等待超时, 硬取消剩余 turn")
            await asyncio.gather(
                *(r.cancel() for r in active if not r.is_terminal),
                return_exceptions=True,
            )


# ---------------------------------------------------------------------------
# 进程内单例
# ---------------------------------------------------------------------------
_SINGLETON: ChatTurnSupervisor | None = None


def get_chat_supervisor() -> ChatTurnSupervisor:
    """获取进程内单例 supervisor. 第一次调用时创建."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = ChatTurnSupervisor()
    return _SINGLETON


def reset_chat_supervisor() -> None:
    """测试 / reload 重置. 不会自动 shutdown 旧实例!"""
    global _SINGLETON
    _SINGLETON = None


__all__ = [
    "ChatTurnSupervisor",
    "get_chat_supervisor",
    "reset_chat_supervisor",
]
