"""GuardLifecycleAdapter: 把 list[LoopGuard] 适配为单个 AgentLifecycle.

让旧 LoopGuard 体系无侵入接入新 lifecycle 协议. 各 guard 自身不变, adapter
只做 StepContext → LoopState 的字段映射 + 合并多个 guidance.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from forge.agents.lifecycle import (
    NoopLifecycle,
    RunContext,
    StepContext,
    StepDecision,
    StepOutcome,
)
from forge.chat.guards.base import Guidance, LoopGuard, LoopState
from forge.core.types.message import Message, ToolCall

logger = logging.getLogger(__name__)


class GuardLifecycleAdapter(NoopLifecycle):
    """把 LoopGuard 列表适配为 AgentLifecycle."""

    def __init__(self, guards: list[LoopGuard]) -> None:
        self._guards = list(guards)

    async def on_start(self, ctx: RunContext) -> None:
        return None

    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        return None

    async def before_step(self, step: StepContext) -> StepDecision | None:
        # 抽上一步第一个 tool_call 的 name + args_hash 给 StuckDetector 用
        last_name: str | None = None
        last_args_hash: str | None = None
        if step.last_step_tool_calls:
            first = step.last_step_tool_calls[0]
            last_name = getattr(first, "name", None)
            args = getattr(first, "arguments", None)
            last_args_hash = _hash_args(args)

        state = LoopState(
            step_index=step.step_index,
            max_steps=step.max_steps,
            last_tool_name=last_name,
            last_tool_args_hash=last_args_hash,
            last_step_tool_calls=step.last_step_tool_calls,
            accumulated_tokens=int(step.accumulated_usage.get("total_tokens", 0) or 0),
            elapsed_seconds=step.elapsed_seconds,
        )

        inject: list[str] = []
        force_stop = False
        for g in self._guards:
            try:
                guidance: Guidance | None = await g.before_step(state)
            except Exception:  # noqa: BLE001
                logger.exception("guard %s 失败, 跳过", type(g).__name__)
                continue
            if guidance is None:
                continue
            inject.append(guidance.content)
            if guidance.severity == "force_stop":
                force_stop = True

        if not inject and not force_stop:
            return None
        return StepDecision(
            inject_system_messages=inject,
            force_text_only=force_stop,
        )

    async def after_step(self, step: StepContext, outcome: StepOutcome) -> None:
        return None

    async def before_tool_call(self, tc: ToolCall, step: StepContext):
        return None

    async def on_tool_result(self, tc: ToolCall, msg: Message) -> Message | None:
        return None

    async def on_complete(self, result) -> None:
        return None

    async def on_error(self, exc: BaseException, partial) -> None:
        return None


def _hash_args(args: Any) -> str | None:
    """对 tool args 做 canonical JSON 哈希, 给 StuckDetector 比对用."""
    if args is None:
        return None
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = str(args)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


__all__ = ["GuardLifecycleAdapter"]
