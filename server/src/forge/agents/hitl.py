"""HITL (Human-in-the-Loop) 通用决策协议.

模型: 主 agent 在关键节点 (Plan Mode 退出 / Workflow gate / 高风险工具确认)
通过 DecisionRegistry 注册一个 PendingDecision, 阻塞等待用户回包.

外部通过 POST /v1/decisions/{token} resolve 决策, 主 agent 醒来继续执行.

设计要点:
    - 全局 in-memory registry (单进程足够; 多进程后接 Redis pub/sub 即可平替)
    - 每个 PendingDecision 自带 asyncio.Event, 主 agent await event.wait()
    - TTL 守护: 后台 cleanup_loop 定期扫表, 过期 token 自动 reject
    - kind 字面量是开放的: 阶段 5 用 "plan"; 阶段 7 加 "workflow_gate";
      未来工具级 HITL 用 "tool_confirm" (本次重构不实现触发逻辑)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from forge.utils.id_generator import new_id

logger = logging.getLogger(__name__)

DecisionKind = Literal["plan", "workflow_gate", "tool_confirm"]


@dataclass
class Decision:
    """用户提交的决策结果."""

    approved: bool
    feedback: str = ""
    edited_plan: str | None = None  # 仅 plan kind 用 (用户修改后的计划)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingDecision:
    """主 agent 提交的待决策 token."""

    token: str
    run_id: str | None
    owner_user_id: str | None
    kind: DecisionKind
    payload: dict[str, Any]
    event: asyncio.Event
    created_at: datetime
    ttl_sec: int = 1800
    decided: Decision | None = None

    def expired_at(self) -> datetime:
        return self.created_at.fromtimestamp(
            self.created_at.timestamp() + self.ttl_sec,
            tz=UTC,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return (now - self.created_at).total_seconds() >= self.ttl_sec


class DecisionRegistry:
    """单进程全局 PendingDecision 注册表."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, PendingDecision] = {}

    def create(
        self,
        *,
        kind: DecisionKind,
        payload: dict[str, Any],
        run_id: str | None = None,
        owner_user_id: str | None = None,
        ttl_sec: int = 1800,
    ) -> PendingDecision:
        token = new_id("dec")
        item = PendingDecision(
            token=token,
            run_id=run_id,
            owner_user_id=owner_user_id,
            kind=kind,
            payload=dict(payload),
            event=asyncio.Event(),
            created_at=datetime.now(UTC),
            ttl_sec=ttl_sec,
        )
        with self._lock:
            self._items[token] = item
        logger.info("HITL 等待决策: token=%s kind=%s run=%s", token, kind, run_id)
        return item

    def get(self, token: str) -> PendingDecision | None:
        with self._lock:
            return self._items.get(token)

    def resolve(self, token: str, decision: Decision) -> bool:
        """提交决策. 返回是否成功唤醒 (token 不存在或已过期返回 False)."""
        with self._lock:
            item = self._items.get(token)
            if item is None:
                return False
            if item.decided is not None:
                logger.warning("decision token=%s 重复 resolve, 忽略", token)
                return False
            if item.is_expired():
                logger.warning("decision token=%s 已过期, 拒绝 resolve", token)
                return False
            item.decided = decision
        item.event.set()
        logger.info(
            "HITL 决策收到: token=%s approved=%s feedback_len=%d",
            token, decision.approved, len(decision.feedback or ""),
        )
        return True

    def remove(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)

    async def cleanup_loop(self, interval_sec: float = 60.0) -> None:
        """后台清理过期 token. 在 lifespan 中 create_task 跑."""
        while True:
            try:
                await asyncio.sleep(interval_sec)
                expired: list[str] = []
                now = datetime.now(UTC)
                with self._lock:
                    for token, item in self._items.items():
                        if item.is_expired(now):
                            expired.append(token)
                for token in expired:
                    with self._lock:
                        expired_item = self._items.get(token)
                        if expired_item and expired_item.decided is None:
                            # 标记拒绝, 唤醒等待方
                            expired_item.decided = Decision(
                                approved=False,
                                feedback="决策超时 (TTL 到达)",
                            )
                            expired_item.event.set()
                            logger.info("HITL 决策超时 token=%s", token)
                    # 不立即 remove: 让 caller 读到 timeout 状态后再 remove
            except asyncio.CancelledError:
                logger.info("DecisionRegistry cleanup_loop 退出")
                return
            except Exception:  # noqa: BLE001
                logger.exception("DecisionRegistry cleanup_loop 异常, 继续")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_registry: DecisionRegistry | None = None
_lock = threading.Lock()


def get_decision_registry() -> DecisionRegistry:
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = DecisionRegistry()
    return _registry


def reset_decision_registry() -> None:
    """测试隔离用."""
    global _registry
    with _lock:
        _registry = None


__all__ = [
    "Decision",
    "DecisionKind",
    "DecisionRegistry",
    "PendingDecision",
    "get_decision_registry",
    "reset_decision_registry",
]
