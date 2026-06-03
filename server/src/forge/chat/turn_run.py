"""ChatTurnRun: 单个 chat turn 的容器.

设计要点 (相比旧 TurnOrchestrator.run_turn 的 async generator 模型):
    - turn 跑在独立 asyncio.Task, **完全脱离** SSE 路由 generator 的生命周期
    - 事件流: agent yields → store.append (fsync) → broadcaster.publish (内存)
    - subscribe() 返回的迭代器先从 events.jsonl 回放, 再接 broadcaster 实时事件
    - SSE 路由的 generator 死掉 / 客户端断开 / 浏览器关闭 都不影响背景 task

正常路径:
    Route -> orchestrator.start_turn -> ChatTurnRun.attach_task(execute_coro)
          -> 返回 run -> 路由 yield run.subscribe()
    背景 task 跑 execute_coro: context manager / runner / finalizer, 中间每个 agent event
    都通过 run.emit() 落盘 + 推订阅者; 完成时 finalizer 写 DB.

中断路径:
    /chat/stop -> supervisor.abort(mid) -> run.abort() -> abort_event.set()
    agent.stream() 检测到 abort_event.is_set() → break + yield done(aborted)
    背景 task 收到 done, 走 finalizer → DB 写 status=aborted + content=已生成内容
    全程 SSE 路由是否还活着都不影响.

崩溃路径:
    背景 task 抛出 → emit error 事件 + DB 写 status=error.
    (进程被 SIGKILL: events.jsonl 已 fsync, 启动恢复扫描后可补 DB; 本期不做.)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from forge.chat.broadcaster import Broadcaster
from forge.chat.event_store import ChatEventStore

logger = logging.getLogger(__name__)


# 终态: 与 RunResult.finish_reason / DB status 对齐
TERMINAL_OK = "completed"
TERMINAL_ABORTED = "aborted"
TERMINAL_ERROR = "error"
TERMINAL_PARTIAL = "partial"

_TERMINAL_SET = frozenset({TERMINAL_OK, TERMINAL_ABORTED, TERMINAL_ERROR, TERMINAL_PARTIAL})


class ChatTurnRun:
    """一个进行中的或已完成的 chat turn.

    属性:
        message_id: 对应 DB chat_messages 表的 assistant 消息 id, 也是 chat_runs/ 子目录名
        session_id: chat session id
        user_id: 拥有该 turn 的用户 id (鉴权用)
        store: ChatEventStore — events.jsonl + state.json
        broadcaster: 实时事件广播器
        abort_event: asyncio.Event, /chat/stop 设置
        is_resume: True 表示该 turn 是 resume 续写 (不是新对话)
    """

    def __init__(
        self,
        *,
        message_id: str,
        session_id: str,
        user_id: str,
        is_resume: bool = False,
        trace_id: str = "",
        on_unhandled_error: Callable[[str], Awaitable[None]] | None = None,
        on_cancelled: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.message_id = message_id
        self.session_id = session_id
        self.user_id = user_id
        self.is_resume = is_resume
        self.trace_id = trace_id
        self._on_unhandled_error = on_unhandled_error
        self._on_cancelled = on_cancelled

        self.store = ChatEventStore(message_id)
        self.broadcaster = Broadcaster()
        self.abort_event = asyncio.Event()
        # subscribe 时,实际生效的回放下界 = max(last_seq, baseline_seq).
        # 用途: resume 场景下旧 events.jsonl 已被前端消费过, 不应再重放.
        # fresh resume 在 start_resume 里把它设成"resume 起跑时的 max seq".
        # reattach 也可以由路由层补设, 防止断线后客户端无 last_seq 触发重放.
        self.baseline_seq: int = 0

        self._task: asyncio.Task | None = None
        self._terminal_status: str | None = None
        self._done = asyncio.Event()
        self.created_at = datetime.now(UTC)
        self.finished_at: datetime | None = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self._done.is_set()

    @property
    def terminal_status(self) -> str | None:
        return self._terminal_status

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    # ------------------------------------------------------------------
    # 启动 / 等待
    # ------------------------------------------------------------------
    def attach_task(self, coro: Awaitable[str]) -> asyncio.Task:
        """把执行协程包成背景 task. coro 必须返回终态字符串.

        wrap 层负责: 兜底异常 / close broadcaster / 写 state.json finished / set _done.
        """
        if self._task is not None:
            raise RuntimeError(f"TurnRun {self.message_id} 已绑定 task")
        self._task = asyncio.create_task(
            self._wrap_execution(coro),
            name=f"chat_turn:{self.message_id}",
        )
        return self._task

    async def _wrap_execution(self, coro: Awaitable[str]) -> str:
        """背景 task 顶层 wrapper. 保证 finally 一定跑."""
        terminal = TERMINAL_ERROR
        try:
            await self.store.save_state({
                "status": "running",
                "session_id": self.session_id,
                "user_id": self.user_id,
                "is_resume": self.is_resume,
                "trace_id": self.trace_id,
                "created_at": self.created_at.isoformat(),
            })
            terminal = await coro
            if terminal not in _TERMINAL_SET:
                logger.warning(
                    "execute coro 返回未知终态 %s, 视为 error message_id=%s",
                    terminal, self.message_id,
                )
                terminal = TERMINAL_ERROR
        except asyncio.CancelledError:
            # 背景 task 被取消 (例如 supervisor.shutdown)
            logger.info("TurnRun 被取消 message_id=%s", self.message_id)
            terminal = TERMINAL_ABORTED
            cancel_message = "服务关闭, turn 被取消"
            # 补一个 error 事件让订阅者知道
            with suppress(Exception):
                await self.emit({
                    "type": "error",
                    "message": cancel_message,
                    "code": "50001",
                })
            await self._notify_cancelled(cancel_message)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("TurnRun 背景任务异常 message_id=%s", self.message_id)
            terminal = TERMINAL_ERROR
            error_message = str(exc) or exc.__class__.__name__
            with suppress(Exception):
                await self.emit({
                    "type": "error",
                    "message": error_message,
                    "code": "50000",
                })
            await self._notify_unhandled_error(error_message)
        finally:
            self._terminal_status = terminal
            self.finished_at = datetime.now(UTC)
            try:
                await self.store.update_state(
                    status=terminal,
                    finished_at=self.finished_at.isoformat(),
                )
            except Exception:  # noqa: BLE001
                logger.exception("写 state.json 终态失败 message_id=%s", self.message_id)
            # 关广播器, 唤醒所有 subscribe() 协程
            self.broadcaster.close()
            self._done.set()
        return terminal

    async def _notify_unhandled_error(self, message: str) -> None:
        """异常未走到 finalizer 时，兜底更新业务消息状态。"""
        if self._on_unhandled_error is None:
            return
        try:
            await self._on_unhandled_error(message)
        except Exception:  # noqa: BLE001
            logger.exception("兜底写入 error 状态失败 message_id=%s", self.message_id)

    async def _notify_cancelled(self, message: str) -> None:
        """服务关闭硬取消时，兜底更新业务消息状态。"""
        if self._on_cancelled is None:
            return
        try:
            await self._on_cancelled(message)
        except Exception:  # noqa: BLE001
            logger.exception("兜底写入 aborted 状态失败 message_id=%s", self.message_id)

    async def wait_done(self) -> str:
        """阻塞到背景 task 跑完, 返回终态."""
        await self._done.wait()
        return self._terminal_status or TERMINAL_ERROR

    # ------------------------------------------------------------------
    # 事件 emit (供 execute coro 调用)
    # ------------------------------------------------------------------
    async def emit(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """落盘 + 广播一个事件. 返回带 seq + ts 的完整记录.

        event_dict 形如 {"type": "delta", "content": "..."}; 见 SSE 协议.
        """
        record = await self.store.append_event(event_dict)
        await self.broadcaster.publish(record)
        return record

    # ------------------------------------------------------------------
    # subscribe — 回放 + 实时合流
    # ------------------------------------------------------------------
    async def subscribe(
        self,
        *,
        last_seq: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """订阅事件流.

        协议:
            1. 先 yield 全部 seq > last_seq 的历史事件 (从 events.jsonl);
               这样客户端断线重连只要带上 last_seq 就能不丢不重.
            2. 若 turn 已是终态, 历史里已包含终态事件, 直接结束.
            3. 否则订阅 broadcaster, yield 实时事件, 跳过 seq <= max(已 yield 的 seq).
            4. broadcaster 关闭 (turn 结束) → 自然 break.

        异常 / 客户端断开 → 调用方迭代器结束 → 自动 unsubscribe (finally).
        """
        # 先订阅 broadcaster, 防止"回放 → 实时"之间的事件丢失
        sub = self.broadcaster.subscribe()
        # baseline_seq 兜底: resume / reattach 场景下, 客户端默认 last_seq=0
        # 但旧 events.jsonl 已被前端消费, 重放会造成内容翻倍 + 过早终态事件.
        effective_last_seq = max(last_seq, self.baseline_seq)
        try:
            max_seen = effective_last_seq
            async for record in self.store.iter_events(after_seq=effective_last_seq):
                yield record
                try:
                    s = int(record.get("seq", 0))
                    if s > max_seen:
                        max_seen = s
                except (TypeError, ValueError):
                    pass

            # turn 已终, broadcaster 也 closed; 后续 async for 立刻收 sentinel 退出
            async for record in sub:
                try:
                    s = int(record.get("seq", 0))
                except (TypeError, ValueError):
                    s = 0
                # 去重: 回放可能已经包含了某些 seq
                if s <= max_seen:
                    continue
                max_seen = s
                yield record
        finally:
            sub.unsubscribe()

    # ------------------------------------------------------------------
    # 中断
    # ------------------------------------------------------------------
    def abort(self) -> None:
        """signal 背景 task 该收尾了. 实际收尾由 agent / finalizer 完成."""
        self.abort_event.set()

    async def cancel(self) -> None:
        """硬取消背景 task (服务关停时用)."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await self._task


__all__ = [
    "ChatTurnRun",
    "TERMINAL_OK",
    "TERMINAL_ABORTED",
    "TERMINAL_ERROR",
    "TERMINAL_PARTIAL",
]
