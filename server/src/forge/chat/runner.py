"""AgentRunner 抽象 + ReActRunner 实现.

AgentRunner = 一次 turn 内, 真正跑 agent 循环并产事件的角色.

ReActRunner 的职责:
    1. 用 GuardLifecycleAdapter 把 LoopGuard 链接入 AgentLifecycle 协议
    2. 跑 ReActAgent.stream, 透传事件
    3. 累计 RunResult 给 Finalizer 用

未来加入 plan_exec / workflow mode 时, 通过 ReActRunner.from_profile 装配
不同的 lifecycle 数组 (Plan Mode / 持久化 / Workflow gate), 无需新 Runner 类.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable

from forge.agents.base import AgentEvent
from forge.agents.lifecycle import AgentLifecycle, MultiLifecycle, RunContext
from forge.agents.react.agent import ReActAgent
from forge.chat.guards import (
    GuardLifecycleAdapter,
    LoopGuard,
    StepSafetyNet,
    StuckDetector,
    TokenBudgetGuard,
    WallClockGuard,
)
from forge.chat.types import RunResult, TurnContext
from forge.config.domains.agent_profiles import AgentProfile
from forge.core.types.message import Message
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry
from forge.workspace.runtime import (
    WorkspaceRuntimeSettings,
    resolve_runtime_settings,
)

logger = logging.getLogger(__name__)

# Safety net: max_steps 不是业务约束, 是兜底上限.
DEFAULT_MAX_STEPS = 50

GuardFactory = Callable[[int], LoopGuard]

# model_options 里的路由选择字段: 已用于 binding 的 preferred_provider/model,
# 不能作为 per-call extra_options 透传给 provider SDK (provider/model 不是 SDK
# 调用参数, 裸透传会触发 "unexpected keyword argument 'provider'")。
_ROUTE_ONLY_OPTION_KEYS = frozenset({"provider", "model"})


def _sdk_model_options(model_options: dict | None) -> dict | None:
    """从 model_options 剔除路由选择字段, 只保留可透传给 SDK 的 per-call 采样参数
    (thinking / thinking_level 等)。ctx.model_options 本身保持完整 (binding /
    resume / context_window 仍需 provider+model)。"""
    if not model_options:
        return model_options
    cleaned = {
        k: v for k, v in model_options.items() if k not in _ROUTE_ONLY_OPTION_KEYS
    }
    return cleaned or None


def _default_guard_factories(runtime: WorkspaceRuntimeSettings) -> list[GuardFactory]:
    """所有默认 guards. 每个 turn 创建一组新实例 (避免跨 turn 状态污染)."""
    return [
        lambda max_steps: StepSafetyNet(),
        lambda max_steps: StuckDetector(),
        lambda max_steps: TokenBudgetGuard(),
        lambda max_steps: WallClockGuard(
            soft_limit_sec=runtime.wall_clock_soft_limit_sec,
            warn_limit_sec=runtime.wall_clock_warn_limit_sec,
            hard_limit_sec=runtime.wall_clock_hard_limit_sec,
        ),
    ]


class AgentRunner(ABC):
    """所有 agent mode 的统一执行接口."""

    result: RunResult

    @abstractmethod
    def run(
        self,
        ctx: TurnContext,
        messages: list[Message],
        abort_event: asyncio.Event,
    ) -> AsyncIterator[AgentEvent]: ...


class ReActRunner(AgentRunner):
    """ReActAgent.stream 的包装, 用 lifecycle 接入 LoopGuard."""

    def __init__(
        self,
        llm_chain,
        system_prompt: str,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        guard_factories: list[GuardFactory] | None = None,
        extra_lifecycles: list[AgentLifecycle] | None = None,
        role: str = "local",
        tools: Iterable[Tool] | None = None,
    ) -> None:
        self._llm = llm_chain
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._guard_factories = guard_factories
        self._extra_lifecycles = list(extra_lifecycles or [])
        self._role = role
        self._tools = list(tools) if tools is not None else None
        self.result: RunResult = RunResult()

    async def run(
        self,
        ctx: TurnContext,
        messages: list[Message],
        abort_event: asyncio.Event,
    ) -> AsyncIterator[AgentEvent]:
        """跑 ReAct stream, 透传事件, 同时累计 RunResult."""
        # 装配 lifecycle: GuardLifecycleAdapter 一定有; extra_lifecycles 由
        # 上层 (后续 Profile 体系) 追加 Plan Mode / 持久化 / Workflow 等.
        guards: list[LoopGuard] = [
            factory(self._max_steps) for factory in self._build_guard_factories()
        ]
        lifecycles: list[AgentLifecycle] = [GuardLifecycleAdapter(guards)]
        lifecycles.extend(self._extra_lifecycles)
        lifecycle = MultiLifecycle(lifecycles)

        run_ctx = RunContext(
            run_id=None,  # chat 路径不通过 RunStore, 此处保持 None
            mode=ctx.agent_mode,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            metadata={"trace_id": ctx.trace_id},
        )

        agent = ReActAgent(
            self._llm,
            system_prompt=self._system_prompt,
            max_steps=self._max_steps,
            role=self._role,
            tools=self._tools,
        )

        # messages 里包含 system + history + current_user, ReActAgent 自己会再加 system.
        # 把 system 和 current_user 切掉, 只留 history.
        history = messages[1:-1] if len(messages) >= 2 else []
        assembled_user_msg = (
            messages[-1].content if len(messages) >= 1 else ctx.current_user_message
        )

        async for event in agent.stream(
            assembled_user_msg,
            history=history,
            abort_event=abort_event,
            model_options=_sdk_model_options(ctx.model_options),
            lifecycle=lifecycle,
            run_ctx=run_ctx,
        ):
            event_dict = event.to_dict()

            if event.type == "done":
                # 累计终态, 不下发 done (Finalizer 自己发统一格式的 done/partial/error)
                self.result.content = event_dict.get("content", "") or ""
                self.result.tool_calls = event_dict.get("tool_calls", None) or []
                self.result.reasoning_content = event_dict.get("reasoning_content")
                self.result.reasoning_duration_ms = event_dict.get("reasoning_duration_ms")
                self.result.usage = event_dict.get("usage") or {}
                # R5: 把 ReActAgent 的 "length" (max_steps 抵达 / LLM 自身截断)
                # 翻译成 "partial_steps". "aborted" / "stop" 不变.
                raw = event_dict.get("finish_reason", "stop")
                self.result.finish_reason = "partial_steps" if raw == "length" else raw
                return

            if event.type == "error":
                self.result.error_message = event_dict.get("message", "")
                self.result.content = event_dict.get("content", "") or ""
                self.result.tool_calls = event_dict.get("tool_calls", None) or []
                self.result.usage = event_dict.get("usage") or {}
                self.result.finish_reason = "error"
                return

            yield event

    def _build_guard_factories(self) -> list[GuardFactory]:
        if self._guard_factories is not None:
            return self._guard_factories
        runtime = resolve_runtime_settings()
        return _default_guard_factories(runtime)

    # ------------------------------------------------------------------
    # Profile 驱动的工厂方法 (mode 路由的唯一入口)
    # ------------------------------------------------------------------
    @classmethod
    def from_profile(
        cls,
        llm_chain,
        profile: AgentProfile,
        *,
        system_prompt: str,
        extra_lifecycles: list[AgentLifecycle] | None = None,
        role: str = "local",
    ) -> ReActRunner:
        """按 Profile 装配 Runner.

        - tools_allowed → 从 ToolRegistry 过滤实际 Tool 实例
        - max_steps → 兜底上限
        - extra_lifecycles → 调用方按需追加 Plan Mode / 持久化 / Workflow 等
          (阶段 5/6/7 会在 Runner 外部装配, 这里不内置)
        """
        tools = tuple(
            t for t in ToolRegistry.get_all() if t.name in set(profile.tools_allowed)
        )
        missing = sorted(set(profile.tools_allowed) - {t.name for t in tools})
        if missing:
            # 启动期已校验, 运行期不该再出现; 这里 warn 防御
            logger.warning(
                "profile.tools_allowed 含未注册工具 (启动校验应已拦截): %s", missing
            )
        return cls(
            llm_chain,
            system_prompt=system_prompt,
            max_steps=profile.max_steps,
            extra_lifecycles=extra_lifecycles,
            role=role,
            tools=tools,
        )
