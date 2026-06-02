"""Plan Mode 实现 (Claude Code 风格).

核心机制 (动态工具集 + HITL gate):
    - PLAN_MODE: ContextVar[bool] 在 run 期间标记当前是否处于 Plan 阶段
    - resolve_tools 每步前返回不同 schemas:
        PLAN_MODE=True  -> readonly_schemas (只读 + exit_plan_mode)
        PLAN_MODE=False -> full_schemas (写工具解锁, 不含 exit_plan_mode)
    - before_tool_call 拦截 exit_plan_mode 调用, 在 lifecycle 内完成:
        1. 通过 DecisionRegistry 创建 PendingDecision (kind="plan")
        2. await event.wait() 阻塞主流程, 等用户回包
        3. 据 approved 设置 PLAN_MODE 状态
        4. 返回 ToolCallVeto 给 ReActAgent 当作 tool_result

设计要点:
    - 不修改 ToolExecutor (锁是 agent 层语义, 与执行器解耦)
    - 不依赖 LLM 自律 (物理看不到写工具 schema, 不会浪费 token 试错)
    - 不真实执行 exit_plan_mode 工具 (它只是 schema, 实际逻辑在 lifecycle)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from contextvars import ContextVar

from forge.agents.hitl import (
    Decision,
    DecisionRegistry,
    get_decision_registry,
)
from forge.agents.lifecycle import NoopLifecycle, RunContext, StepContext, ToolCallVeto
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.run_store import RunStore

logger = logging.getLogger(__name__)


# ContextVar 默认 False (chat / Exec 阶段都是 False)
PLAN_MODE: ContextVar[bool] = ContextVar("PLAN_MODE", default=False)


def is_plan_mode() -> bool:
    return PLAN_MODE.get()


def set_plan_mode(value: bool) -> None:
    PLAN_MODE.set(value)


class PlanModeLifecycle(NoopLifecycle):
    """Plan Mode 主 lifecycle.

    Args:
        readonly_schemas: Plan 阶段对 LLM 暴露的工具 (一般 = 只读 + exit_plan_mode)
        full_schemas:     Exec 阶段对 LLM 暴露的工具 (写工具 + spawn_subagent 等,
                          不含 exit_plan_mode)
        run_id:           当前 run, 用于 DecisionRegistry 关联归属
        registry:         可选注入, 默认走全局单例
        ttl_sec:          PendingDecision 超时时间 (默认 30 分钟)
    """

    def __init__(
        self,
        readonly_schemas: list[dict],
        full_schemas: list[dict],
        *,
        run_id: str | None = None,
        owner_user_id: str | None = None,
        store: RunStore | None = None,
        registry: DecisionRegistry | None = None,
        ttl_sec: int = 1800,
    ) -> None:
        self._readonly = list(readonly_schemas)
        self._full = list(full_schemas)
        self._run_id = run_id
        self._owner_user_id = owner_user_id
        self._store = store
        self._registry = registry or get_decision_registry()
        self._ttl_sec = ttl_sec

    async def on_start(self, ctx: RunContext) -> None:
        PLAN_MODE.set(True)
        if self._run_id is None:
            self._run_id = ctx.run_id
        if self._owner_user_id is None:
            self._owner_user_id = ctx.user_id
        logger.info(
            "Plan Mode 进入: run=%s readonly_tools=%d full_tools=%d",
            ctx.run_id, len(self._readonly), len(self._full),
        )

    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        return self._readonly if PLAN_MODE.get() else self._full

    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        """拦截 exit_plan_mode 调用, 走 HITL 流程."""
        if tc.name != "exit_plan_mode":
            return None
        if not PLAN_MODE.get():
            # 已经在 Exec 阶段, 不该再调 (LLM 看不到 schema 也走不到这里, 但兜底)
            return ToolCallVeto(
                blocked=True,
                reason="not_in_plan_mode",
                replacement_message=Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="[plan_mode] 当前不处于 Plan 阶段, 无需调用 exit_plan_mode.",
                ),
            )

        plan_md = str(tc.arguments.get("plan_markdown") or "").strip()
        if not plan_md:
            return ToolCallVeto(
                blocked=True,
                reason="empty_plan",
                replacement_message=Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="[plan_mode] plan_markdown 不能为空, 请提交完整计划再退出.",
                ),
            )

        pending = self._registry.create(
            kind="plan",
            payload={"plan_markdown": plan_md},
            run_id=self._run_id,
            owner_user_id=self._owner_user_id,
            ttl_sec=self._ttl_sec,
        )
        await self._mark_blocked(pending.token)
        logger.info(
            "Plan 提交, 等待用户决策: token=%s run=%s plan_len=%d",
            pending.token, self._run_id, len(plan_md),
        )

        with suppress(TimeoutError):
            # cleanup_loop 应该已经标记 decided = Decision(False, "决策超时")
            await asyncio.wait_for(pending.event.wait(), timeout=self._ttl_sec + 5)

        decided: Decision | None = pending.decided
        self._registry.remove(pending.token)
        await self._mark_running(pending.token, decided)

        if decided is None or not decided.approved:
            feedback = (decided.feedback if decided else "决策超时") or "未批准"
            content = json.dumps(
                {
                    "approved": False,
                    "token": pending.token,
                    "feedback": feedback,
                    "next": "保持 Plan Mode, 请根据反馈调整计划后再次调用 exit_plan_mode.",
                },
                ensure_ascii=False,
            )
            return ToolCallVeto(
                blocked=True,
                reason="plan_rejected",
                replacement_message=Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=content,
                ),
            )

        # 批准: 解锁 Plan Mode
        PLAN_MODE.set(False)
        logger.info("Plan 已批准, 进入 Exec 阶段: run=%s token=%s", self._run_id, pending.token)
        content = json.dumps(
            {
                "approved": True,
                "token": pending.token,
                "edited_plan": decided.edited_plan,
                "feedback": decided.feedback or "",
                "next": (
                    "计划已批准, 写工具已解锁. 按计划执行, 完成后给出综合报告."
                ),
            },
            ensure_ascii=False,
        )
        return ToolCallVeto(
            blocked=True,
            reason="plan_approved",
            replacement_message=Message(
                role="tool",
                tool_call_id=tc.id,
                name=tc.name,
                content=content,
            ),
        )

    async def _mark_blocked(self, token: str) -> None:
        if self._store is None or self._run_id is None:
            return
        payload = {"reason": "plan_decision_required", "token": token}
        try:
            await self._store.transition_status(self._run_id, "blocked", payload=payload)
            await self._store.append_event(self._run_id, "plan_decision_required", payload)
        except Exception:  # noqa: BLE001
            logger.exception("Plan Mode blocked 状态写入失败 run=%s", self._run_id)

    async def _mark_running(self, token: str, decided: Decision | None) -> None:
        if self._store is None or self._run_id is None:
            return
        payload = {
            "reason": "plan_decision_received",
            "token": token,
            "approved": bool(decided and decided.approved),
        }
        try:
            await self._store.transition_status(self._run_id, "running", payload=payload)
        except Exception:  # noqa: BLE001
            logger.exception("Plan Mode running 状态恢复失败 run=%s", self._run_id)


__all__ = ["PLAN_MODE", "PlanModeLifecycle", "is_plan_mode", "set_plan_mode"]
