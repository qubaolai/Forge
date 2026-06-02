"""ReAct Agent (function-calling 风格).

经典 ReAct (Reason+Act) 模式, 但用 LLM 原生 function calling 而非
"Thought / Action / Observation" 文本解析. 工业实现里这个更稳定.

流程:
    1. 把 system prompt + history + user input 喂给 LLM (带 tools schema)
    2. 模型要么直接答复, 要么返回 tool_calls
    3. 有 tool_calls: 执行所有工具, 把 tool 消息追加, 回到第 2 步
    4. 无 tool_calls 或步数到顶: 结束

扩展点: 通过 AgentLifecycle 协议接入. 没有 lifecycle 时行为等同纯 ReAct.
"""

import asyncio
import logging
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Iterable,
)
from typing import Any, cast

from forge.agents.base import AgentEvent, AgentResult, BaseAgent
from forge.agents.lifecycle import (
    AgentLifecycle,
    RunContext,
    RunResult,
    StepContext,
    StepOutcome,
)
from forge.core.request_context import current_client_type
from forge.core.types.errors import AgentMaxStepsError
from forge.core.types.message import Message, ToolCall
from forge.llm.contracts import ToolCallingLLM
from forge.observability.tracing.tracer import span
from forge.prompts import get_registry
from forge.tools.executor import ToolExecutor, get_default_executor
from forge.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _default_system_prompt() -> str:
    """从 PromptRegistry 加载 ReAct 默认系统提示 (prompts/react/system.j2)."""
    return get_registry().render("react/system")


def _update_record(
    accumulated_tool_calls: list[dict],
    tc_id: str,
    status: str,
    result_str: str,
) -> None:
    """根据 tc_id 找到 accumulated 中的占位记录, 填 status / result."""
    for record in accumulated_tool_calls:
        if record["id"] == tc_id:
            record["status"] = status
            record["result"] = result_str
            if status == "error":
                record["error_message"] = result_str
            break


class ReActAgent(BaseAgent):
    def __init__(
        self,
        llm: ToolCallingLLM,  # 通常是 LLMFallbackChain (已绑定 provider/model spec)
        *,
        tools: Iterable | None = None,  # Iterable[Tool], None = 取 ToolRegistry.all
        system_prompt: str | None = None,
        max_steps: int = 8,
        executor: ToolExecutor | None = None,
        role: str = "local",
    ) -> None:
        self._llm = llm
        self._max_steps = max_steps
        self._system_prompt = system_prompt or _default_system_prompt()
        self._role = role
        if tools is not None:
            # 自定义工具集: 单独算 schema (不命中 ToolRegistry 缓存)
            self._tools_list = list(tools)
            self._default_tool_schemas = [t.openai_schema() for t in self._tools_list]
        else:
            # 默认全集: 走 ToolRegistry 的注册期预算缓存
            self._tools_list = ToolRegistry.get_all()
            self._default_tool_schemas = ToolRegistry.openai_schemas()
        # ToolExecutor 无可变状态, 默认复用全局单例; 测试 / 隔离场景可显式注入.
        self._executor = executor or get_default_executor()
        logger.info(
            "ReActAgent 就绪: tools=%s max_steps=%d",
            [t.name for t in self._tools_list],
            max_steps,
        )

    async def run(
        self,
        user_input: str,
        *,
        history: list[Message] | None = None,
    ) -> AgentResult:
        messages: list[Message] = [Message(role="system", content=self._system_prompt)]
        if history:
            messages.extend(history)
        messages.append(Message(role="user", content=user_input))

        total_usage: dict[str, int] = {}
        steps = 0
        with span("agent.react.run", max_steps=self._max_steps) as outer:
            for step in range(self._max_steps):
                steps = step + 1
                with span("agent.react.step", step=steps) as s:
                    resp = await self._call_llm(messages)
                    self._merge_usage(total_usage, resp.get("usage") or {})
                    s.set("tool_calls", len(resp["tool_calls"]))

                    if not resp["tool_calls"]:
                        # 终态: 模型直接给答案
                        msg = Message(role="assistant", content=resp["content"])
                        messages.append(msg)
                        outer.set("steps", steps)
                        outer.set("final", True)
                        return AgentResult(
                            output=resp["content"],
                            messages=messages,
                            steps=steps,
                            usage=total_usage,
                            metadata={"model": resp.get("model", "")},
                        )

                    # 中间态: 把 assistant 的 tool_calls 消息加上
                    messages.append(
                        Message(
                            role="assistant",
                            content=resp["content"],
                            tool_calls=resp["tool_calls"],
                            extra_content=resp.get("reasoning_content"),
                        )
                    )

                    # 顺序执行每个 tool call (并行版本以后再优化)
                    for tc in resp["tool_calls"]:
                        with span("agent.react.tool", tool=tc.name) as ts:
                            try:
                                tool_msg = await self._executor.aexecute(tc, role=self._role)
                                ts.set("ok", True)
                            except Exception as e:  # noqa: BLE001
                                ts.set_error(e)
                                tool_msg = Message(
                                    role="tool",
                                    content=f"[tool error] {e}",
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                )
                            messages.append(tool_msg)

        outer.set("steps", steps)
        outer.set("final", False)
        raise AgentMaxStepsError(self._max_steps)

    async def stream(
        self,
        user_input: str,
        *,
        history: list[Message] | None = None,
        message_id: str | None = None,
        session_id: str | None = None,
        abort_event: Any | None = None,
        model_options: dict[str, Any] | None = None,
        lifecycle: AgentLifecycle | None = None,
        run_ctx: RunContext | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """ReAct 真流式执行.

        底层走 chat_with_tools_stream: content token 边生成边吐 delta,
        tool_calls 在 finish_reason='tool_calls' 时一次性产出.

        Args:
            lifecycle: 生命周期扩展点 (mode / 持久化 / Plan Mode / HITL 都通过它接入).
                None 表示纯 ReAct, 行为与 max_steps + 默认 tool_schemas 等价.
            run_ctx: 传给 lifecycle.on_start 的 run 静态上下文.
        """
        messages: list[Message] = [Message(role="system", content=self._system_prompt)]
        if history:
            messages.extend(history)
        messages.append(Message(role="user", content=user_input))

        if message_id and session_id:
            yield AgentEvent(
                "message_start",
                {"message_id": message_id, "session_id": session_id},
            )

        total_usage: dict[str, int] = {}
        accumulated_content = ""
        accumulated_tool_calls: list[dict] = []
        finish_reason = "stop"
        # 跨 step 拼接的 reasoning (DeepSeek thinking 等), 用于最终落库 / 调试
        accumulated_reasoning = ""
        # 思考累计墙钟时间 (毫秒)
        accumulated_reasoning_ms = 0

        # lifecycle.on_start: 在主循环前调一次
        if lifecycle is not None:
            try:
                await lifecycle.on_start(run_ctx or RunContext())
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle.on_start 失败, 主流程继续")

        run_started_at = time.monotonic()

        try:
            with span(
                "agent.react.stream",  # ← 顶层 span
                max_steps=self._max_steps,
                user_input_len=len(user_input),
                history_count=len(history or []),
            ) as outer:
                # 上一步的 tool_calls (StuckDetector / before_step 用)
                last_step_tool_calls: tuple[ToolCall, ...] = ()
                for _step in range(self._max_steps):
                    if abort_event and abort_event.is_set():
                        finish_reason = "aborted"
                        break

                    step_ctx = StepContext(
                        step_index=_step,
                        max_steps=self._max_steps,
                        messages_count=len(messages),
                        last_step_tool_calls=last_step_tool_calls,
                        accumulated_usage=dict(total_usage),
                        elapsed_seconds=time.monotonic() - run_started_at,
                    )

                    # lifecycle.resolve_tools: 动态工具集核心 (Plan Mode 切 readonly/full)
                    current_tool_schemas = self._default_tool_schemas
                    if lifecycle is not None:
                        try:
                            resolved = await lifecycle.resolve_tools(step_ctx)
                            if resolved is not None:
                                current_tool_schemas = resolved
                        except Exception:  # noqa: BLE001
                            logger.exception("lifecycle.resolve_tools 失败, 用默认 schemas")

                    # lifecycle.before_step: 注入引导 / 决定强制纯文本
                    force_text_only = False
                    if lifecycle is not None:
                        try:
                            decision = await lifecycle.before_step(step_ctx)
                        except Exception:  # noqa: BLE001
                            logger.exception("lifecycle.before_step 失败, 本步跳过引导")
                            decision = None
                        if decision is not None:
                            for sys_text in decision.inject_system_messages:
                                messages.append(Message(role="system", content=sys_text))
                                logger.info(
                                    "ReAct 注入引导 step=%d injected_len=%d force_text_only=%s",
                                    _step + 1,
                                    len(sys_text),
                                    decision.force_text_only,
                                )
                            force_text_only = decision.force_text_only

                    step_started_at = time.perf_counter()
                    step_content = ""
                    step_tool_calls: list[ToolCall] = []
                    step_finish: str | None = None
                    step_usage: dict[str, int] = {}
                    step_reasoning = ""
                    step_reasoning_start: float | None = None
                    reasoning_phase_open = False

                    # ---- 流式 LLM 调用（span 只覆盖推理阶段，不含工具执行）----
                    with span(
                        "agent.react.llm_call",
                        step=_step + 1,
                        messages_count=len(messages),
                        tool_count=len(current_tool_schemas),
                    ) as llm_span:
                        try:
                            _tool_choice = "none" if force_text_only else "auto"
                            chunk_iter = self._llm.chat_with_tools_stream(
                                messages,
                                current_tool_schemas,
                                extra_options=model_options,
                                tool_choice=_tool_choice,
                            )
                        except Exception as e:
                            logger.exception("chat_with_tools_stream 失败")
                            llm_span.set_error(e)
                            err_ev = self._make_error_event(
                                e, accumulated_content, accumulated_tool_calls, total_usage
                            )
                            await self._invoke_on_error(
                                lifecycle, e,
                                accumulated_content, accumulated_tool_calls, total_usage,
                                accumulated_reasoning, accumulated_reasoning_ms,
                            )
                            yield err_ev
                            return

                        reasoning_phase_open = True

                        try:
                            async for chunk in chunk_iter:
                                if abort_event and abort_event.is_set():
                                    finish_reason = "aborted"
                                    break

                                delta_text = chunk.get("content_delta", "") or ""
                                if delta_text:
                                    if step_reasoning_start is not None:
                                        accumulated_reasoning_ms += int(
                                            (time.perf_counter() - step_reasoning_start) * 1000
                                        )
                                        step_reasoning_start = None
                                    if reasoning_phase_open:
                                        yield AgentEvent(
                                            "reasoning_end",
                                            {"reasoning_duration_ms": accumulated_reasoning_ms},
                                        )
                                        reasoning_phase_open = False
                                    step_content += delta_text
                                    accumulated_content += delta_text
                                    yield AgentEvent("delta", {"content": delta_text})

                                reasoning_delta = chunk.get("reasoning_delta") or ""
                                if reasoning_delta:
                                    if step_reasoning_start is None:
                                        step_reasoning_start = time.perf_counter()
                                    step_reasoning += reasoning_delta
                                    accumulated_reasoning += reasoning_delta
                                    yield AgentEvent(
                                        "reasoning_delta",
                                        {"content": reasoning_delta},
                                    )

                                if chunk.get("tool_calls"):
                                    step_tool_calls = chunk["tool_calls"]
                                if chunk.get("usage"):
                                    step_usage = chunk["usage"]
                                if chunk.get("finish_reason"):
                                    step_finish = chunk["finish_reason"]
                        except Exception as e:  # noqa: BLE001
                            logger.exception("ReAct stream chunk 处理失败")
                            llm_span.set_error(e)
                            if step_reasoning_start is not None:
                                accumulated_reasoning_ms += int(
                                    (time.perf_counter() - step_reasoning_start) * 1000
                                )
                                step_reasoning_start = None
                            if reasoning_phase_open:
                                yield AgentEvent(
                                    "reasoning_end",
                                    {"reasoning_duration_ms": accumulated_reasoning_ms},
                                )
                                reasoning_phase_open = False
                            err_ev = self._make_error_event(
                                e, accumulated_content, accumulated_tool_calls, total_usage
                            )
                            await self._invoke_on_error(
                                lifecycle, e,
                                accumulated_content, accumulated_tool_calls, total_usage,
                                accumulated_reasoning, accumulated_reasoning_ms,
                            )
                            yield err_ev
                            return

                        # 本 step 结束时计时器收尾
                        if step_reasoning_start is not None:
                            accumulated_reasoning_ms += int(
                                (time.perf_counter() - step_reasoning_start) * 1000
                            )
                            step_reasoning_start = None
                        if reasoning_phase_open:
                            yield AgentEvent(
                                "reasoning_end",
                                {"reasoning_duration_ms": accumulated_reasoning_ms},
                            )
                            reasoning_phase_open = False

                        llm_span.set("output_tokens", step_usage.get("completion_tokens", 0))
                        llm_span.set("tool_calls_count", len(step_tool_calls))
                        llm_span.set("finish_reason", step_finish or "stop")
                    # ---- llm_call span 到此结束，工具执行是兄弟节点 ----

                    logger.info(
                        "ReAct 单步完成 step=%d/%d content_len=%d reasoning_len=%d "
                        "tool_calls=%s finish_reason=%s client_type=%s usage=%s",
                        _step + 1,
                        self._max_steps,
                        len(step_content),
                        len(step_reasoning),
                        [tc.name for tc in step_tool_calls] or "[]",
                        step_finish or "-",
                        current_client_type(),
                        step_usage or {},
                    )

                    self._merge_usage(total_usage, step_usage)

                    if abort_event and abort_event.is_set():
                        finish_reason = "aborted"
                        break

                    # ---- 没有 tool_calls: 终态, 已经流完所有 delta ----
                    if not step_tool_calls:
                        finish_reason = step_finish or "stop"
                        # 调 after_step (这步无工具调用, outcome.tool_calls 空)
                        await self._invoke_after_step(
                            lifecycle, step_ctx, _step, step_content,
                            [], step_usage, finish_reason, step_started_at,
                        )
                        break

                    # ---- 有 tool_calls: 把 assistant 消息加上, 执行工具 ----
                    messages.append(
                        Message(
                            role="assistant",
                            content=step_content,
                            tool_calls=step_tool_calls,
                            extra_content=step_reasoning or None,
                        )
                    )

                    # 阶段 1: 先把所有 tool_call 事件按 LLM 给的顺序 yield 出去
                    for tc in step_tool_calls:
                        tc_record = {
                            "id": tc.id,
                            "tool_id": tc.name,
                            "tool_name": tc.name,
                            "arguments": tc.arguments,
                            "status": "running",
                        }
                        accumulated_tool_calls.append(tc_record)
                        yield AgentEvent("tool_call", {"tool_call": tc_record})

                    # 阶段 2: 执行 (含 lifecycle.before_tool_call 拦截 + on_tool_result 替换)
                    results_by_id: dict[str, Message] = {}
                    i = 0
                    while i < len(step_tool_calls):
                        # 阶段性 abort 检查: 用户中断信号到来时, 不再派发尚未启动
                        # 的工具调用. 已在 flight 的工具不强行打断 (避免副作用),
                        # 留给本步循环外的 finish_reason=aborted 兜底.
                        if abort_event and abort_event.is_set():
                            finish_reason = "aborted"
                            logger.info(
                                "工具循环检测到 abort, 跳过未派发工具 step=%d 剩余=%d",
                                _step + 1, len(step_tool_calls) - i,
                            )
                            # 补 aborted 的 tool_result, 前端不卡 running
                            for skipped in step_tool_calls[i:]:
                                _update_record(
                                    accumulated_tool_calls, skipped.id,
                                    "aborted", "用户中断, 工具未执行",
                                )
                                yield AgentEvent(
                                    "tool_result",
                                    {
                                        "tool_call_id": skipped.id,
                                        "result": "用户中断, 工具未执行",
                                        "status": "aborted",
                                    },
                                )
                            break
                        cur = step_tool_calls[i]
                        if self._executor.is_parallelism_safe(cur.name):
                            # 收齐一段连续 safe 工具
                            j = i + 1
                            while j < len(step_tool_calls) and self._executor.is_parallelism_safe(
                                step_tool_calls[j].name
                            ):
                                j += 1
                            batch = step_tool_calls[i:j]
                            if len(batch) == 1:
                                async for ev in self._execute_one_yield(
                                    batch[0], _step, accumulated_tool_calls, results_by_id,
                                    lifecycle, step_ctx,
                                ):
                                    yield ev
                            else:
                                # 多条 safe -> 并行
                                tasks = [
                                    asyncio.create_task(
                                        self._execute_with_lifecycle(tc, _step, lifecycle, step_ctx)
                                    )
                                    for tc in batch
                                ]
                                for fut in asyncio.as_completed(tasks):
                                    tc, tool_msg, status, result_str = await fut
                                    results_by_id[tc.id] = tool_msg
                                    _update_record(
                                        accumulated_tool_calls, tc.id, status, result_str
                                    )
                                    yield AgentEvent(
                                        "tool_result",
                                        {
                                            "tool_call_id": tc.id,
                                            "result": result_str,
                                            "status": status,
                                        },
                                    )
                            i = j
                        else:
                            async for ev in self._execute_one_yield(
                                cur, _step, accumulated_tool_calls, results_by_id,
                                lifecycle, step_ctx,
                            ):
                                yield ev
                            i += 1

                    # 阶段 3: 把 tool 消息按 LLM 原始顺序追加到 messages.
                    # abort 路径下 results_by_id 可能缺末尾若干项, 跳过即可
                    # (这一步用于喂下一轮 LLM, abort 后不会有下一轮).
                    for tc in step_tool_calls:
                        if tc.id in results_by_id:
                            messages.append(results_by_id[tc.id])

                    # 记录本步 tool_calls 给下一步 lifecycle.before_step 用
                    last_step_tool_calls = tuple(step_tool_calls)

                    # 调 after_step
                    await self._invoke_after_step(
                        lifecycle, step_ctx, _step, step_content,
                        step_tool_calls, step_usage, step_finish or "tool_calls",
                        step_started_at,
                    )
                else:
                    finish_reason = "length"

                # 顶层 span 记录整体结果
                outer.set("total_steps", _step + 1)
                outer.set("finish_reason", finish_reason)
                outer.set("total_tokens", total_usage.get("total_tokens", 0))

            # lifecycle.on_complete
            if lifecycle is not None:
                final_result = RunResult(
                    finish_reason=finish_reason,
                    content=accumulated_content,
                    tool_calls=list(accumulated_tool_calls),
                    reasoning_content=accumulated_reasoning or None,
                    reasoning_duration_ms=accumulated_reasoning_ms or None,
                    usage={
                        "prompt_tokens": total_usage.get("prompt_tokens", 0),
                        "completion_tokens": total_usage.get("completion_tokens", 0),
                        "total_tokens": total_usage.get("total_tokens", 0),
                    },
                )
                try:
                    await lifecycle.on_complete(final_result)
                except Exception:  # noqa: BLE001
                    logger.exception("lifecycle.on_complete 失败")

            yield AgentEvent(
                "done",
                {
                    "usage": {
                        "prompt_tokens": total_usage.get("prompt_tokens", 0),
                        "completion_tokens": total_usage.get("completion_tokens", 0),
                        "total_tokens": total_usage.get("total_tokens", 0),
                    },
                    "finish_reason": finish_reason,
                    "content": accumulated_content,
                    "tool_calls": accumulated_tool_calls or None,
                    "reasoning_content": accumulated_reasoning or None,
                    "reasoning_duration_ms": accumulated_reasoning_ms or None,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("ReAct stream 异常")
            await self._invoke_on_error(
                lifecycle, e,
                accumulated_content, accumulated_tool_calls, total_usage,
                accumulated_reasoning, accumulated_reasoning_ms,
            )
            yield self._make_error_event(
                e, accumulated_content, accumulated_tool_calls, total_usage
            )

    @staticmethod
    async def _iter_async(sync_iter) -> AsyncGenerator[dict, None]:
        """把同步迭代器变成异步, 每次 next 跑在线程池, 避免阻塞事件循环."""
        loop = asyncio.get_event_loop()
        _sentinel = object()
        while True:
            value = await loop.run_in_executor(None, next, sync_iter, _sentinel)
            if value is _sentinel:
                return
            yield cast(dict[str, Any], value)

    # ------------------------------------------------------------------
    # 工具执行辅助 (含 lifecycle.before_tool_call / on_tool_result)
    # ------------------------------------------------------------------
    async def _execute_with_lifecycle(
        self,
        tc: ToolCall,
        step_idx: int,
        lifecycle: AgentLifecycle | None,
        step_ctx: StepContext,
    ) -> tuple[ToolCall, Message, str, str]:
        """跑一个工具, 经过 lifecycle 拦截 + 结果替换. 不 yield."""
        # 1. before_tool_call 拦截
        if lifecycle is not None:
            try:
                veto = await lifecycle.before_tool_call(tc, step_ctx)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle.before_tool_call 失败, 默认放行")
                veto = None
            if veto is not None and veto.blocked:
                msg = veto.replacement_message or Message(
                    role="tool",
                    content=f"[blocked] {veto.reason or '工具调用被拦截'}",
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                # 拦截后仍要走 on_tool_result 让持久化层有机会处理
                msg = await self._maybe_replace_msg(lifecycle, tc, msg)
                return tc, msg, "blocked", msg.content

        # 2. 真实执行
        _tool_start = time.perf_counter()
        with span("agent.react.tool", tool=tc.name, step=step_idx + 1) as ts:
            try:
                tool_msg = await self._executor.aexecute(tc, role=self._role)
                status = "success"
                result_str = tool_msg.content
                ts.set("ok", True)
                ts.set("result_len", len(result_str or ""))
            except Exception as e:  # noqa: BLE001
                logger.exception("工具 %s 执行失败", tc.name)
                ts.set_error(e)
                tool_msg = Message(
                    role="tool",
                    content=f"[tool error] {e}",
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                status = "error"
                result_str = str(e)
            ts.set("duration_ms", round((time.perf_counter() - _tool_start) * 1000, 1))

        # 3. on_tool_result 替换 (持久化层把大产物落 artifact 回灌占位)
        tool_msg = await self._maybe_replace_msg(lifecycle, tc, tool_msg)
        return tc, tool_msg, status, tool_msg.content

    @staticmethod
    async def _maybe_replace_msg(
        lifecycle: AgentLifecycle | None,
        tc: ToolCall,
        msg: Message,
    ) -> Message:
        if lifecycle is None:
            return msg
        try:
            replaced = await lifecycle.on_tool_result(tc, msg)
        except Exception:  # noqa: BLE001
            logger.exception("lifecycle.on_tool_result 失败")
            return msg
        return replaced if replaced is not None else msg

    async def _execute_one_yield(
        self,
        tc: ToolCall,
        step_idx: int,
        accumulated_tool_calls: list[dict],
        results_by_id: dict[str, Message],
        lifecycle: AgentLifecycle | None,
        step_ctx: StepContext,
    ) -> AsyncIterator[AgentEvent]:
        """跑一个工具, 把 tool_result event yield 出来, 顺便 update 累计列表."""
        tc, tool_msg, status, result_str = await self._execute_with_lifecycle(
            tc, step_idx, lifecycle, step_ctx
        )
        results_by_id[tc.id] = tool_msg
        _update_record(accumulated_tool_calls, tc.id, status, result_str)
        yield AgentEvent(
            "tool_result",
            {
                "tool_call_id": tc.id,
                "result": result_str,
                "status": status,
            },
        )

    # ------------------------------------------------------------------
    # lifecycle 辅助
    # ------------------------------------------------------------------
    @staticmethod
    async def _invoke_after_step(
        lifecycle: AgentLifecycle | None,
        step_ctx: StepContext,
        step_index: int,
        step_content: str,
        step_tool_calls: list[ToolCall],
        step_usage: dict[str, int],
        finish_reason: str,
        step_started_at: float,
    ) -> None:
        if lifecycle is None:
            return
        outcome = StepOutcome(
            step_index=step_index,
            content=step_content,
            tool_calls=list(step_tool_calls),
            usage=dict(step_usage),
            finish_reason=finish_reason,
            duration_ms=(time.perf_counter() - step_started_at) * 1000,
        )
        try:
            await lifecycle.after_step(step_ctx, outcome)
        except Exception:  # noqa: BLE001
            logger.exception("lifecycle.after_step 失败")

    @staticmethod
    async def _invoke_on_error(
        lifecycle: AgentLifecycle | None,
        exc: BaseException,
        content: str,
        tool_calls: list[dict],
        usage: dict[str, int],
        reasoning_content: str,
        reasoning_duration_ms: int,
    ) -> None:
        if lifecycle is None:
            return
        partial = RunResult(
            finish_reason="error",
            content=content,
            tool_calls=list(tool_calls),
            reasoning_content=reasoning_content or None,
            reasoning_duration_ms=reasoning_duration_ms or None,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            error_message=str(exc),
        )
        try:
            await lifecycle.on_error(exc, partial)
        except Exception:  # noqa: BLE001
            logger.exception("lifecycle.on_error 失败")

    @staticmethod
    def _make_error_event(
        exc: BaseException,
        content: str,
        tool_calls: list[dict],
        usage: dict,
    ) -> AgentEvent:
        return AgentEvent(
            "error",
            {
                "message": f"模型调用失败: {exc}",
                "content": content,
                "tool_calls": tool_calls or None,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            },
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _call_llm(self, messages: list[Message]) -> dict:
        """调用 LLM 拿 tool_calls + content.

        优先走整条 fallback 链 (带 retry + 成本记账); 拿不到链才退化到单 LLM.
        """
        return await self._llm.chat_with_tools(messages, self._default_tool_schemas)

    @staticmethod
    def _merge_usage(total: dict[str, int], delta: dict) -> None:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = delta.get(k)
            if isinstance(v, int | float):
                total[k] = total.get(k, 0) + int(v)
