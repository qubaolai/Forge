"""R7 Tool 并行执行单测.

覆盖:
    1. Tool 默认 parallelism_safe=True
    2. ToolExecutor.is_parallelism_safe 正确返回各情况
    3. ReActAgent.stream 全 safe -> 并行 (验证墙钟比串行短)
    4. ReActAgent.stream 全 unsafe -> 串行 (顺序保留)
    5. 混合 -> 连续 safe 段并行, 单条 unsafe 串行, 最终 tool 消息按 LLM 原始顺序追加
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from forge.agents.react.agent import ReActAgent
from forge.core.types.message import Message, ToolCall
from forge.tools.base import Tool
from forge.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# 测试用 Tool
# ---------------------------------------------------------------------------
class _SleepTool(Tool):
    """sleep N 秒返回. 用墙钟差异验证是否并行."""

    parameters = {"type": "object", "properties": {"sec": {"type": "number"}}, "required": ["sec"]}

    def __init__(self, name: str, parallelism_safe: bool = True) -> None:
        self.name = name
        self.description = f"sleep {name}"
        self.parallelism_safe = parallelism_safe

    def run(self, args: dict[str, Any]) -> str:
        time.sleep(args["sec"])  # 故意 sync, 模拟阻塞 IO
        return f"{self.name}-done"


def _make_executor(*tools: Tool) -> ToolExecutor:
    """造一个临时 ToolExecutor + 临时 registry, 避免污染全局."""
    fake = type("FakeRegistry", (), {})()
    by_name = {t.name: t for t in tools}
    fake.get = lambda name: by_name.get(name)
    fake.get_all = lambda: list(tools)
    return ToolExecutor(registry=fake)


# ---------------------------------------------------------------------------
# Tool.parallelism_safe 默认值
# ---------------------------------------------------------------------------
def test_tool_default_parallelism_safe_is_true() -> None:
    class _Pure(Tool):
        name = "pure"
        description = "x"
        parameters = {"type": "object"}

        def run(self, args):
            return "ok"

    assert _Pure().parallelism_safe is True


def test_tool_can_opt_out() -> None:
    class _Unsafe(Tool):
        name = "unsafe"
        description = "x"
        parameters = {"type": "object"}
        parallelism_safe = False

        def run(self, args):
            return "ok"

    assert _Unsafe().parallelism_safe is False


# ---------------------------------------------------------------------------
# ToolExecutor.is_parallelism_safe
# ---------------------------------------------------------------------------
def test_executor_is_parallelism_safe_for_registered() -> None:
    executor = _make_executor(
        _SleepTool("a", parallelism_safe=True),
        _SleepTool("b", parallelism_safe=False),
    )
    assert executor.is_parallelism_safe("a") is True
    assert executor.is_parallelism_safe("b") is False


def test_executor_is_parallelism_safe_unknown_is_false() -> None:
    """未注册的工具按 unsafe 处理 (保守)."""
    executor = _make_executor()
    assert executor.is_parallelism_safe("not_registered") is False


# ---------------------------------------------------------------------------
# ReActAgent 集成: 并行 vs 串行墙钟
# ---------------------------------------------------------------------------
def _stub_llm_with_calls(tool_calls_per_step: list[list[ToolCall]]):
    """造一个最简 LLM stub: 按预设的 step 返回 tool_calls, 最后一步无 tool_calls (终态).

    chat_with_tools_stream 是同步迭代器, 返回 chunk dict.
    """

    class _Stub:
        def __init__(self):
            self.step = 0

        def chat_with_tools_stream(self, messages, tools, **kwargs):
            calls = tool_calls_per_step[self.step] if self.step < len(tool_calls_per_step) else []
            self.step += 1
            # 单 chunk: 给 content_delta = "" + tool_calls + finish_reason
            yield {
                "content_delta": "" if calls else "最终答复",
                "tool_calls": calls,
                "finish_reason": "tool_calls" if calls else "stop",
                "usage": {"total_tokens": 10},
            }

    return _Stub()


async def _collect_events(stream) -> list:
    out = []
    async for ev in stream:
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_all_safe_tools_run_in_parallel() -> None:
    """三个 safe 工具各 sleep 0.2s; 并行总耗时应远小于串行 0.6s."""
    tools = [_SleepTool(f"t{i}", parallelism_safe=True) for i in range(3)]
    executor = _make_executor(*tools)
    calls = [ToolCall(id=f"tc{i}", name=f"t{i}", arguments={"sec": 0.2}) for i in range(3)]

    agent = ReActAgent(
        llm=_stub_llm_with_calls([calls, []]),
        tools=tools,
        executor=executor,
        max_steps=3,
    )

    t0 = time.perf_counter()
    events = await _collect_events(agent.stream("hi"))
    elapsed = time.perf_counter() - t0

    # 并行: 单次 ~0.2s + 一点点开销; 串行会 ~0.6s+
    assert elapsed < 0.5, f"应该并行但耗时 {elapsed:.2f}s"
    # 三个 tool_result 事件都到了
    result_events = [e for e in events if e.type == "tool_result"]
    assert len(result_events) == 3
    assert {e.payload["tool_call_id"] for e in result_events} == {"tc0", "tc1", "tc2"}


@pytest.mark.asyncio
async def test_all_unsafe_tools_run_serially() -> None:
    """三个 unsafe 工具各 sleep 0.15s; 串行总耗时应接近 0.45s."""
    tools = [_SleepTool(f"u{i}", parallelism_safe=False) for i in range(3)]
    executor = _make_executor(*tools)
    calls = [ToolCall(id=f"tc{i}", name=f"u{i}", arguments={"sec": 0.15}) for i in range(3)]

    agent = ReActAgent(
        llm=_stub_llm_with_calls([calls, []]),
        tools=tools,
        executor=executor,
        max_steps=3,
    )

    t0 = time.perf_counter()
    await _collect_events(agent.stream("hi"))
    elapsed = time.perf_counter() - t0

    # 串行: 3 * 0.15s = 0.45s 起步
    assert elapsed >= 0.4, f"应该串行但耗时 {elapsed:.2f}s (太快了)"


@pytest.mark.asyncio
async def test_mixed_preserves_order_in_messages() -> None:
    """混合 safe/unsafe 时, 最终 tool 消息追加顺序 = LLM 给的顺序."""
    tools = [
        _SleepTool("safe1", parallelism_safe=True),
        _SleepTool("unsafe", parallelism_safe=False),
        _SleepTool("safe2", parallelism_safe=True),
    ]
    executor = _make_executor(*tools)
    calls = [
        ToolCall(id="tc1", name="safe1", arguments={"sec": 0.01}),
        ToolCall(id="tc2", name="unsafe", arguments={"sec": 0.01}),
        ToolCall(id="tc3", name="safe2", arguments={"sec": 0.01}),
    ]

    captured_messages: list[Message] = []

    class _CaptureLLM:
        def __init__(self):
            self.step = 0
            self.last_messages: list[Message] = []

        def chat_with_tools_stream(self, messages, tools_, **kwargs):
            self.last_messages = list(messages)
            calls_now = calls if self.step == 0 else []
            self.step += 1
            yield {
                "content_delta": "" if calls_now else "done",
                "tool_calls": calls_now,
                "finish_reason": "tool_calls" if calls_now else "stop",
                "usage": {"total_tokens": 10},
            }
            # 第二轮被调时, messages 应该已经包含 tool 消息按顺序追加
            captured_messages.clear()
            captured_messages.extend(self.last_messages)

    llm = _CaptureLLM()
    agent = ReActAgent(llm=llm, tools=tools, executor=executor, max_steps=3)
    await _collect_events(agent.stream("hi"))

    # 第二轮 LLM 调用看到的 messages: 末尾应该是 [assistant_with_tool_calls,
    # tool(tc1), tool(tc2), tool(tc3)]
    tool_msgs = [m for m in captured_messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["tc1", "tc2", "tc3"]
