"""ReAct Agent (function-calling 风格).

经典 ReAct (Reason+Act) 模式, 但用 LLM 原生 function calling 而非
"Thought / Action / Observation" 文本解析. 工业实现里这个更稳定.

流程:
    1. 把 system prompt + history + user input 喂给 LLM (带 tools schema)
    2. 模型要么直接答复, 要么返回 tool_calls
    3. 有 tool_calls: 执行所有工具, 把 tool 消息追加, 回到第 2 步
    4. 无 tool_calls 或步数到顶: 结束

为何不实现文本解析的 ReAct:
    - 模型经常输出格式不一致 (如 'Thought:' 没换行), 解析脆弱
    - 现代 LLM 都原生支持 function calling, 直接用更稳
    - 兼容 OpenAI / DeepSeek / DashScope (compat) / 后续 Anthropic
"""

import asyncio
import logging
import threading
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
)
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from forge.agents.base import AgentEvent, AgentResult, BaseAgent
from forge.core.request_context import current_client_type
from forge.core.types.errors import AgentMaxStepsError
from forge.core.types.message import Message
from forge.observability.tracing.tracer import span
from forge.prompts import get_registry
from forge.tools.executor import ToolExecutor, get_default_executor
from forge.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# before_step 钩子: 给上层 (chat.Runner / LoopGuard) 注入引导的轻量接口.
# 不让 ReActAgent 知道 "LoopGuard" 概念, 只懂 "上层可以让我注入 system 消息 +
# 强制纯文本输出". 这样 agents 层和 chat 层的耦合最小.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StepContext:
    """每一步 LLM 调用前传给 before_step 的快照."""

    step_index: int  # 0-based
    max_steps: int  # 配置的 safety net 上限
    messages_count: int  # 当前 messages 列表长度
    last_step_tool_calls: tuple = ()  # 上一步 LLM 返回的 tool_calls (空表示第一步或上步无工具)
    accumulated_usage: dict = field(default_factory=dict)
    # 截至本步开始 (不含本步) 累计的 token 用量, 含 prompt_tokens / completion_tokens / total_tokens.
    # R4 TokenBudgetGuard 用


@dataclass(frozen=True)
class StepDecision:
    """before_step 的返回. 默认是 "什么都不做"."""

    inject_system_messages: list[str] = field(default_factory=list)
    # 追加到 messages 末尾的 system 消息 (LLM 当作最近的引导看)

    force_text_only: bool = False
    # 这一步把 tool_choice 改为 "none", 强制 LLM 不调工具, 只输出文本


BeforeStepHook = Callable[[StepContext], Awaitable[StepDecision]]


def _run_awaitable_blocking(factory: Callable[[], Awaitable[Message]]) -> Message:
    """在同步入口阻塞等待异步工具执行，兼容已有 run() 调用方。

    正常情况下 run() 会被放到 worker thread 里执行，可以直接 asyncio.run。
    如果误在已有 event loop 的线程内调用 run()，则新开短线程承载事件循环，
    避免嵌套 asyncio.run() 失败。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    box: dict[str, Message | BaseException] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=_runner, name="react-tool-sync-bridge", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]


class ToolCallingLLM(Protocol):
    """ReAct 只依赖已绑定模型配置的 tool-calling facade."""

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> dict: ...

    def chat_with_tools_stream(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]: ...


def _default_system_prompt() -> str:
    """从 PromptRegistry 加载 ReAct 默认系统提示 (prompts/react/system.j2).

    懒加载避免模块导入期 import PromptRegistry, 也允许 chat 路由传入
    自己的 system_prompt (来自 chat/default_system.j2) 完全覆盖.
    """
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
            self._tool_schemas = [t.openai_schema() for t in self._tools_list]
        else:
            # 默认全集: 走 ToolRegistry 的注册期预算缓存
            self._tools_list = ToolRegistry.get_all()
            self._tool_schemas = ToolRegistry.openai_schemas()
        # ToolExecutor 无可变状态, 默认复用全局单例; 测试 / 隔离场景可显式注入.
        self._executor = executor or get_default_executor()
        logger.info(
            "ReActAgent 就绪: tools=%s max_steps=%d",
            [t.name for t in self._tools_list],
            max_steps,
        )

    def run(
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
                    resp = self._call_llm(messages)
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
                            reasoning_content=resp.get("reasoning_content"),
                        )
                    )

                    # 顺序执行每个 tool call (并行版本以后再优化)
                    for tc in resp["tool_calls"]:
                        with span("agent.react.tool", tool=tc.name) as ts:
                            try:
                                tool_msg = self._execute_tool_blocking(tc)
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

    def _execute_tool_blocking(self, tc) -> Message:
        """同步 run() 入口也统一走 aexecute()，避免异步工具被误调 run()."""
        return _run_awaitable_blocking(lambda: self._executor.aexecute(tc, role=self._role))

    async def stream(
        self,
        user_input: str,
        *,
        history: list[Message] | None = None,
        message_id: str | None = None,
        session_id: str | None = None,
        abort_event: Any | None = None,
        model_options: dict[str, Any] | None = None,
        before_step: BeforeStepHook | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """ReAct 真流式执行.

        底层走 chat_with_tools_stream: content token 边生成边吐 delta,
        tool_calls 在 finish_reason='tool_calls' 时一次性产出.

        Args:
            before_step: 可选钩子, 每步 LLM 调用前被调一次. 上层 (chat.Runner)
                用它桥接 LoopGuard, 决定是否注入 system 引导 / 强制纯文本输出.
                ReActAgent 本身不感知 LoopGuard.
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
        # 思考累计墙钟时间 (毫秒): 每个 step 从首个 reasoning_delta 到首个 content_delta
        # (或该 step 结束) 的耗时. 仅统计 DeepSeek thinking / 类似机制实际产生 reasoning 的时间.
        accumulated_reasoning_ms = 0

        try:
            with span(
                "agent.react.stream",  # ← 顶层 span
                max_steps=self._max_steps,
                user_input_len=len(user_input),
                history_count=len(history or []),
            ) as outer:
                # 上一步的 tool_calls (给 before_step 看, R4 StuckDetector 用)
                last_step_tool_calls: tuple = ()
                for _step in range(self._max_steps):
                    if abort_event and abort_event.is_set():
                        finish_reason = "aborted"
                        break

                    # ★ before_step 钩子: 上层注入引导. ReActAgent 自己不知道
                    # 为什么注入, 只负责执行 StepDecision.
                    force_text_only = False
                    if before_step is not None:
                        try:
                            decision = await before_step(
                                StepContext(
                                    step_index=_step,
                                    max_steps=self._max_steps,
                                    messages_count=len(messages),
                                    last_step_tool_calls=last_step_tool_calls,
                                    accumulated_usage=dict(total_usage),
                                )
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception("before_step hook 失败, 本步跳过引导")
                            decision = StepDecision()
                        for sys_text in decision.inject_system_messages:
                            messages.append(Message(role="system", content=sys_text))
                            logger.info(
                                "ReAct 注入引导 step=%d injected_len=%d force_text_only=%s",
                                _step + 1,
                                len(sys_text),
                                decision.force_text_only,
                            )
                        force_text_only = decision.force_text_only

                    step_content = ""
                    step_tool_calls: list = []
                    step_finish: str | None = None
                    step_usage: dict = {}
                    step_reasoning = ""
                    # 本 step 的思考起点; 首个 reasoning_delta 到达时记录,
                    # 首个 content_delta 出现 (或 step 结束) 时累加到 accumulated_reasoning_ms 并清零
                    step_reasoning_start: float | None = None
                    # 统一思考阶段信号: reasoning_delta 由 provider 可选产出 (仅 thinking 模型),
                    # 首个 content_delta 或 tool_call 前发 reasoning_end 附带已用时.
                    reasoning_phase_open = False

                    # ---- 流式 LLM 调用（span 只覆盖推理阶段，不含工具执行）----
                    with span(
                        "agent.react.llm_call", step=_step + 1, messages_count=len(messages)
                    ) as llm_span:
                        try:
                            # force_text_only -> tool_choice="none", 让 LLM 这步必须输出文本
                            # 不能调工具. 用于 StepSafetyNet 的最后一步强制收尾.
                            _tool_choice = "none" if force_text_only else "auto"

                            def _start_stream(
                                tool_choice: str = _tool_choice,
                            ) -> Iterator[dict[str, Any]]:
                                return self._llm.chat_with_tools_stream(
                                    messages,
                                    self._tool_schemas,
                                    extra_options=model_options,
                                    tool_choice=tool_choice,
                                )

                            chunk_iter = await asyncio.to_thread(_start_stream)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("chat_with_tools_stream 失败")
                            llm_span.set_error(e)
                            yield self._make_error_event(
                                e, accumulated_content, accumulated_tool_calls, total_usage
                            )
                            return

                        reasoning_phase_open = True

                        try:
                            async for chunk in self._iter_async(chunk_iter):
                                if abort_event and abort_event.is_set():
                                    finish_reason = "aborted"
                                    break

                                delta_text = chunk.get("content_delta", "") or ""
                                if delta_text:
                                    # 首个 content 抵达: 先合计本 step 思考用时, 再关阶段
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

                                # reasoning 增量 (provider 可选, 仅 thinking 模式产生)
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
                            yield self._make_error_event(
                                e, accumulated_content, accumulated_tool_calls, total_usage
                            )
                            return

                        # 本 step 结束时若计时器还开着 (整步只有 reasoning 没 content) 也累加
                        if step_reasoning_start is not None:
                            accumulated_reasoning_ms += int(
                                (time.perf_counter() - step_reasoning_start) * 1000
                            )
                            step_reasoning_start = None

                        # 本 step 流式结束后若思考阶段还开着, 在 tool_call / abort / 空响应
                        # 之前关闭, 保证前端能切回"普通"渲染状态.
                        if reasoning_phase_open:
                            yield AgentEvent(
                                "reasoning_end", {"reasoning_duration_ms": accumulated_reasoning_ms}
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
                        break

                    # ---- 有 tool_calls: 把 assistant 消息加上, 顺序执行工具 ----
                    # reasoning_content 写到 Message, 下一轮请求时 DeepSeek 的
                    # _messages_payload 会回灌, 不写会 400
                    messages.append(
                        Message(
                            role="assistant",
                            content=step_content,
                            tool_calls=step_tool_calls,
                            reasoning_content=step_reasoning or None,
                        )
                    )

                    # 阶段 1: 先把所有 tool_call 事件按 LLM 给的顺序 yield 出去,
                    # 同时往 accumulated_tool_calls 注册占位 (status=running).
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

                    # 阶段 2: 执行. 连续的 parallelism_safe 工具组合并并行执行,
                    # 遇到 unsafe 先排干当前并行批次再串行执行 unsafe.
                    # 这样保证: 全是 safe -> 全并行; 全是 unsafe -> 全串行;
                    # 混合 -> 保留 LLM 顺序的同时, 安全块仍能并行加速.
                    results_by_id: dict[str, Message] = {}
                    i = 0
                    while i < len(step_tool_calls):
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
                                # 单条 safe, 直接跑 (没必要 gather)
                                async for ev in self._execute_one_yield(
                                    batch[0], _step, accumulated_tool_calls, results_by_id
                                ):
                                    yield ev
                            else:
                                # 多条 safe -> 并行, 用 as_completed 让 tool_result
                                # 谁先跑完谁先 yield (用户体验更好).
                                tasks = [
                                    asyncio.create_task(self._execute_one(tc, _step))
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
                            # 单条 unsafe, 串行执行
                            async for ev in self._execute_one_yield(
                                cur, _step, accumulated_tool_calls, results_by_id
                            ):
                                yield ev
                            i += 1

                    # 阶段 3: 把 tool 消息按 LLM 原始顺序追加到 messages
                    # (OpenAI 期望 tool 消息和 assistant.tool_calls 顺序对齐).
                    for tc in step_tool_calls:
                        messages.append(results_by_id[tc.id])

                    # 记录本步 tool_calls 给下一步的 before_step 钩子用
                    last_step_tool_calls = tuple(step_tool_calls)
                else:
                    finish_reason = "length"

                # 顶层span记录整体结果
                outer.set("total_steps", _step + 1)
                outer.set("finish_reason", finish_reason)
                outer.set("total_tokens", total_usage.get("total_tokens", 0))

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
    # 工具执行辅助 (R7 并行支持)
    # ------------------------------------------------------------------
    async def _execute_one(self, tc, step_idx: int) -> tuple[Any, Message, str, str]:
        """跑一个工具, 返回 (tc, tool_msg, status, result_str). 不 yield."""
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
        return tc, tool_msg, status, result_str

    async def _execute_one_yield(
        self,
        tc,
        step_idx: int,
        accumulated_tool_calls: list[dict],
        results_by_id: dict[str, Message],
    ) -> AsyncIterator[AgentEvent]:
        """跑一个工具, 把 tool_result event yield 出来, 顺便 update 累计列表."""
        tc, tool_msg, status, result_str = await self._execute_one(tc, step_idx)
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
    def _call_llm(self, messages: list[Message]) -> dict:
        """调用 LLM 拿 tool_calls + content.

        优先走整条 fallback 链 (带 retry + 成本记账); 拿不到链才退化到单 LLM.
        """
        return self._llm.chat_with_tools(messages, self._tool_schemas)

    @staticmethod
    def _merge_usage(total: dict[str, int], delta: dict) -> None:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = delta.get(k)
            if isinstance(v, int | float):
                total[k] = total.get(k, 0) + int(v)
