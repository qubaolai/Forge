"""Agent 生命周期协议.

ReActAgent 的所有扩展点统一通过 AgentLifecycle 暴露; mode (chat / plan_exec
/ workflow) 差异完全外置为 lifecycle 组合, 不在 ReActAgent 内分支.

调用时机 (按 stream() 循环顺序):
    on_start                     -> run 开始, 整个流只调一次
    每个 step 内:
        resolve_tools            -> 拿本步给 LLM 看的 tool_schemas
                                    (动态工具集核心: Plan/Exec 切换靠它)
        before_step              -> 注入引导 / 决定强制纯文本
        [LLM 调用]
        for tc in tool_calls:
            before_tool_call     -> 拦截工具调用 (Plan Mode 双保险, 未来 HITL)
            [tool 执行 或 用 veto 替代 message]
            on_tool_result       -> 大产物落 artifact 回灌占位 等
        after_step               -> 持久化 checkpoint / 自定义 SSE step 事件
    on_complete                  -> 终态正常完成
    on_error                     -> 终态异常完成

设计原则:
    - AgentLifecycle 的 hook 全部抽象; 选择性实现继承 NoopLifecycle 后只覆写关心的子集.
    - resolve_tools / before_tool_call 多 lifecycle "首个非 None 胜出",
      避免互相覆盖.
    - 其他 hook 全部 lifecycle 都调一次, 副作用各自负责.
    - 单 lifecycle 异常被隔离, 不影响其他 lifecycle 和主流程.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from forge.core.types.message import Message, ToolCall

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 共享数据类型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RunContext:
    """整个 run 的静态上下文 (传给 on_start 用)."""

    run_id: str | None = None  # 关联的持久化 run 标识, chat 模式可为 None
    mode: str = "chat"  # agent_mode (chat / plan_exec / workflow / ...)
    user_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepContext:
    """单步 LLM 调用前传给 lifecycle 的快照."""

    step_index: int  # 0-based
    max_steps: int  # 配置的 safety step 上限
    messages_count: int  # 当前 messages 列表长度
    last_step_tool_calls: tuple[ToolCall, ...] = ()
    accumulated_usage: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0  # 自 run 开始累计墙钟


@dataclass(frozen=True)
class StepDecision:
    """before_step 的返回. 默认 "什么都不做"."""

    inject_system_messages: list[str] = field(default_factory=list)
    # 追加到 messages 末尾的 system 文本 (LLM 当作最近的引导看).

    force_text_only: bool = False
    # 本步 tool_choice="none", 强制 LLM 不调工具, 只输出文本.
    # 用于 max_steps 最后一步收尾 / 死循环 break-out.


@dataclass(frozen=True)
class StepOutcome:
    """单步执行完后的结果快照 (传给 after_step)."""

    step_index: int
    content: str
    tool_calls: list[ToolCall]
    usage: dict[str, int]
    finish_reason: str
    duration_ms: float


@dataclass(frozen=True)
class ToolCallVeto:
    """before_tool_call 的返回. 默认 None = 放行."""

    blocked: bool
    reason: str = ""
    # 若 blocked=True 必须给 replacement_message, ReActAgent 直接当成 tool 结果回灌.
    replacement_message: Message | None = None


@dataclass
class RunResult:
    """on_complete / on_error 传入的累计结果 (与 chat/types.py 的 RunResult 等价).

    放在 lifecycle 模块, 让 lifecycle 实现不必反向依赖 chat.
    """

    finish_reason: str = "stop"
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_content: str | None = None
    reasoning_duration_ms: int | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None


# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------
class AgentLifecycle(ABC):
    """Agent 生命周期扩展 ABC.

    直接子类必须实现全部 hook; 选择性实现应继承 NoopLifecycle.
    """

    @abstractmethod
    async def on_start(self, ctx: RunContext) -> None: ...

    @abstractmethod
    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        """每步前调. 返回 None = 沿用 agent 默认 tool_schemas;
        返回 list = 本步替换为该 schemas (动态工具集核心).
        """
        return None

    @abstractmethod
    async def before_step(self, step: StepContext) -> StepDecision | None:
        """每步前调. 返回 None = 不引导; 返回 StepDecision 决定注入 / 强制纯文本."""
        return None

    @abstractmethod
    async def after_step(self, step: StepContext, outcome: StepOutcome) -> None: ...

    @abstractmethod
    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        """每个工具调用前调. 返回 None = 放行;
        返回 ToolCallVeto(blocked=True) = 用 replacement_message 替代真实执行.
        """
        return None

    @abstractmethod
    async def on_tool_result(self, tc: ToolCall, msg: Message) -> Message | None:
        """工具执行后调. 返回 None = 不改; 返回 Message = 替换 (持久化层用此把大产物落 artifact)."""
        return None

    @abstractmethod
    async def on_complete(self, result: RunResult) -> None: ...

    @abstractmethod
    async def on_error(self, exc: BaseException, partial: RunResult) -> None: ...


# ---------------------------------------------------------------------------
# 空实现 (兜底)
# ---------------------------------------------------------------------------
class NoopLifecycle(AgentLifecycle):
    """没有任何副作用的 lifecycle, 用于不需要 lifecycle 的场景."""

    async def on_start(self, ctx: RunContext) -> None:
        return None

    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        return None

    async def before_step(self, step: StepContext) -> StepDecision | None:
        return None

    async def after_step(self, step: StepContext, outcome: StepOutcome) -> None:
        return None

    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        return None

    async def on_tool_result(self, tc: ToolCall, msg: Message) -> Message | None:
        return None

    async def on_complete(self, result: RunResult) -> None:
        return None

    async def on_error(self, exc: BaseException, partial: RunResult) -> None:
        return None


# ---------------------------------------------------------------------------
# 组合器
# ---------------------------------------------------------------------------
class MultiLifecycle(AgentLifecycle):
    """把多个 lifecycle 串成一个.

    语义:
        on_start / after_step / on_tool_result(累计) / on_complete / on_error
            按注册顺序逐个调; 单个异常被隔离打日志, 不中断其他.
        resolve_tools / before_tool_call / before_step
            "首个非 None 胜出": 找到第一个返回非 None 的 lifecycle 就返回.
            避免多 lifecycle 互相覆盖工具集 / 拦截语义混乱.

    on_tool_result 的"累计"语义: 每个 lifecycle 都可能想替换 msg,
        依次调用, 后者基于前者结果, 形成 pipeline.
    before_step 的"首个胜出": 多个引导合并复杂, 不实现; 真要合并的场景
        建议在调用方拆开两个 lifecycle.
    """

    def __init__(self, lifecycles: Iterable[AgentLifecycle]) -> None:
        self._lifecycles: list[AgentLifecycle] = list(lifecycles)

    @property
    def lifecycles(self) -> list[AgentLifecycle]:
        return list(self._lifecycles)

    async def on_start(self, ctx: RunContext) -> None:
        for lc in self._lifecycles:
            try:
                await lc.on_start(ctx)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.on_start 失败", type(lc).__name__)

    async def resolve_tools(self, step: StepContext) -> list[dict] | None:
        for lc in self._lifecycles:
            try:
                result = await lc.resolve_tools(step)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.resolve_tools 失败", type(lc).__name__)
                continue
            if result is not None:
                return result
        return None

    async def before_step(self, step: StepContext) -> StepDecision | None:
        for lc in self._lifecycles:
            try:
                result = await lc.before_step(step)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.before_step 失败", type(lc).__name__)
                continue
            if result is not None:
                return result
        return None

    async def after_step(self, step: StepContext, outcome: StepOutcome) -> None:
        for lc in self._lifecycles:
            try:
                await lc.after_step(step, outcome)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.after_step 失败", type(lc).__name__)

    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        for lc in self._lifecycles:
            try:
                result = await lc.before_tool_call(tc, step)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "lifecycle %s.before_tool_call 失败", type(lc).__name__
                )
                continue
            if result is not None:
                return result
        return None

    async def on_tool_result(self, tc: ToolCall, msg: Message) -> Message | None:
        current = msg
        replaced = False
        for lc in self._lifecycles:
            try:
                result = await lc.on_tool_result(tc, current)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.on_tool_result 失败", type(lc).__name__)
                continue
            if result is not None:
                current = result
                replaced = True
        return current if replaced else None

    async def on_complete(self, result: RunResult) -> None:
        for lc in self._lifecycles:
            try:
                await lc.on_complete(result)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.on_complete 失败", type(lc).__name__)

    async def on_error(self, exc: BaseException, partial: RunResult) -> None:
        for lc in self._lifecycles:
            try:
                await lc.on_error(exc, partial)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle %s.on_error 失败", type(lc).__name__)


__all__ = [
    "AgentLifecycle",
    "MultiLifecycle",
    "NoopLifecycle",
    "RunContext",
    "RunResult",
    "StepContext",
    "StepDecision",
    "StepOutcome",
    "ToolCallVeto",
]
