"""AgentRunner 抽象 + ReActRunner 实现.

AgentRunner = 一次 turn 内, 真正跑 agent 循环并产事件的角色.

为什么单独一层 (而不是直接用 ReActAgent.stream):
    1. agent.mode 分发: TurnOrchestrator 按 mode 选不同 Runner.
       同一接口下 ReActRunner / PlanExecuteRunner 互换不影响上层.
    2. LoopGuard 接入点: Runner 桥接 LoopGuard 到 ReActAgent 的 before_step 钩子,
       agents 层不感知 LoopGuard.
    3. 累计结果统一打包成 RunResult: Finalizer 不关心是哪个 mode 跑的.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Protocol

from forge.agents.base import AgentEvent
from forge.agents.react.agent import (
    ReActAgent,
    StepContext,
    StepDecision,
)
from forge.chat.guards import (
    LoopGuard,
    LoopState,
    StepSafetyNet,
    StuckDetector,
    TokenBudgetGuard,
    WallClockGuard,
)
from forge.chat.types import RunResult, TurnContext
from forge.core.types.message import Message
from forge.tools.base import Tool
from forge.workspace.runtime import (
    WorkspaceRuntimeSettings,
    resolve_runtime_settings,
)

logger = logging.getLogger(__name__)

# Safety net: max_steps 不再是业务约束, 是兜底上限.
# 配合 StepSafetyNet 在最后几步引导收尾, 真到 50 时模型已经被 force_text_only.
DEFAULT_MAX_STEPS = 50

# 默认 guard 工厂列表. 每个 turn 新建一组实例 (避免跨 turn 状态污染).
# R4 会往这里加 StuckDetector / TokenBudgetGuard / WallClockGuard.
GuardFactory = Callable[[int], LoopGuard]


def _default_guard_factories(runtime: WorkspaceRuntimeSettings) -> list[GuardFactory]:
    """所有默认 guards. 每个 turn 创建一组新实例.

    顺序无关紧要 (任一 force_stop 都生效).
    """
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


class AgentRunner(Protocol):
    """所有 agent mode 的统一执行接口.

    实现方约定:
        - run() yields AgentEvent (跟 ReActAgent.stream 一致, Orchestrator 直接吐 SSE)
        - 跑完后 self.result 必须有值, Finalizer 据此写 DB / 发终态事件
    """

    result: RunResult

    def run(
        self,
        ctx: TurnContext,
        messages: list[Message],
        abort_event: asyncio.Event,
    ) -> AsyncIterator[AgentEvent]: ...


class ReActRunner(AgentRunner):
    """包装 ReActAgent.stream + 桥接 LoopGuard."""

    def __init__(
        self,
        llm_chain,
        system_prompt: str,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        guard_factories: list[GuardFactory] | None = None,
        role: str = "local",
        tools: Iterable[Tool] | None = None,
    ) -> None:
        self._llm = llm_chain
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._guard_factories = guard_factories
        self._role = role
        self._tools = list(tools) if tools is not None else None
        # run() 完成后 finalize 阶段读
        self.result: RunResult = RunResult()

    async def run(
        self,
        ctx: TurnContext,
        messages: list[Message],
        abort_event: asyncio.Event,
    ) -> AsyncIterator[AgentEvent]:
        """跑 ReAct stream, 透传事件, 同时累计 RunResult."""
        # 每个 turn 创建一组新 guard 实例 (避免跨 turn 状态污染, 例如 StuckDetector 的 deque)
        guards: list[LoopGuard] = [
            factory(self._max_steps) for factory in self._build_guard_factories()
        ]
        run_started_at = time.monotonic()
        before_step = self._make_before_step_bridge(guards, run_started_at)

        agent = ReActAgent(
            self._llm,
            system_prompt=self._system_prompt,
            max_steps=self._max_steps,
            role=self._role,
            tools=self._tools,
        )

        # messages 里包含 system + history + current_user, ReActAgent 自己会再加 system.
        # 把 system 和 current_user 切掉, 只留 history.
        # message_id / session_id 故意不传 -- message_start 由 Orchestrator 先发, 避免重复.
        history = messages[1:-1] if len(messages) >= 2 else []
        assembled_user_msg = (
            messages[-1].content if len(messages) >= 1 else ctx.current_user_message
        )
        async for event in agent.stream(
            assembled_user_msg,
            history=history,
            abort_event=abort_event,
            model_options=ctx.model_options,
            before_step=before_step,
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
                # error 不下发, Finalizer 走 error 分支输出统一格式 (跟原 _stream_chat 行为对齐:
                # 原代码: 收到 error -> 更新 DB -> yield error event -> return)
                self.result.error_message = event_dict.get("message", "")
                self.result.content = event_dict.get("content", "") or ""
                self.result.tool_calls = event_dict.get("tool_calls", None) or []
                self.result.usage = event_dict.get("usage") or {}
                self.result.finish_reason = "error"
                return

            # 中间事件 (delta / tool_call / tool_result / reasoning_delta / reasoning_end ...) 直接透传
            yield event

    def _build_guard_factories(self) -> list[GuardFactory]:
        if self._guard_factories is not None:
            return self._guard_factories
        runtime = resolve_runtime_settings()
        return _default_guard_factories(runtime)

    # ------------------------------------------------------------------
    # before_step 桥接: ReActAgent 的钩子 -> LoopGuard 链
    # ------------------------------------------------------------------
    def _make_before_step_bridge(self, guards: list[LoopGuard], run_started_at: float):
        """返回一个 async 函数, 闭包持 guards + 计时引用.

        ReActAgent.stream 调它时传 StepContext, 这里:
            1. 构造 LoopState (含 elapsed_seconds / accumulated_tokens)
            2. 逐 guard.before_step
            3. 合并 guidance + 决定 force_text_only
            4. 返回 StepDecision
        """

        async def bridge(step_ctx: StepContext) -> StepDecision:
            # 抽出上一步第一个 tool_call 的特征 (StuckDetector 用)
            last_name: str | None = None
            last_args_hash: str | None = None
            if step_ctx.last_step_tool_calls:
                first = step_ctx.last_step_tool_calls[0]
                last_name = getattr(first, "name", None)
                args = getattr(first, "arguments", None)
                last_args_hash = _hash_args(args)

            state = LoopState(
                step_index=step_ctx.step_index,
                max_steps=step_ctx.max_steps,
                last_tool_name=last_name,
                last_tool_args_hash=last_args_hash,
                last_step_tool_calls=step_ctx.last_step_tool_calls,
                accumulated_tokens=int(step_ctx.accumulated_usage.get("total_tokens", 0) or 0),
                elapsed_seconds=time.monotonic() - run_started_at,
            )

            inject: list[str] = []
            force_stop = False
            for g in guards:
                try:
                    guidance = await g.before_step(state)
                except Exception:  # noqa: BLE001
                    logger.exception("guard %s 失败, 跳过", type(g).__name__)
                    continue
                if guidance is None:
                    continue
                inject.append(guidance.content)
                if guidance.severity == "force_stop":
                    force_stop = True

            return StepDecision(
                inject_system_messages=inject,
                force_text_only=force_stop,
            )

        return bridge


def _hash_args(args) -> str | None:
    """对 tool args 做 canonical JSON 哈希, 给 StuckDetector 比对用."""
    if args is None:
        return None
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = str(args)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Runner 注册表: agent.mode -> AgentRunner 实现类
# ---------------------------------------------------------------------------
_RUNNER_REGISTRY: dict[str, type[AgentRunner]] = {}


def register_runner(mode: str):
    """类装饰器: 把 AgentRunner 实现登记到 mode 注册表."""

    def deco(cls) -> type[AgentRunner]:
        if mode in _RUNNER_REGISTRY:
            raise ValueError(
                f"agent mode 重复注册: {mode} ("
                f"已存在: {_RUNNER_REGISTRY[mode].__name__}, 新增: {cls.__name__})"
            )
        _RUNNER_REGISTRY[mode] = cls
        return cls

    return deco


def get_runner_class(mode: str) -> type[AgentRunner] | None:
    return _RUNNER_REGISTRY.get(mode)


def supported_modes() -> list[str]:
    return sorted(_RUNNER_REGISTRY.keys())


# 触发 ReActRunner 自注册
register_runner("react")(ReActRunner)
