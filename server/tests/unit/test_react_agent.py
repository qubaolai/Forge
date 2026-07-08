"""测试 ReAct agent (用 mock LLM, 不打外部 API)."""

from __future__ import annotations

import pytest

from forge.agents import ReActAgent
from forge.core.types.errors import AgentMaxStepsError
from forge.core.types.message import ToolCall
from forge.llm.providers.base import LLM
from forge.tools.base import Tool
from forge.tools.executor import ToolExecutor


class _ScriptedLLM(LLM):
    """按预设脚本依次返回, 用来精确测试 ReAct 循环."""

    def __init__(self, script: list[dict]):
        super().__init__(api_key="sk-scripted")
        self._script = list(script)
        self._idx = 0

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def chat_stream(self, messages, **kwargs):
        raise NotImplementedError

    async def chat_with_tools(self, messages, tools, **kwargs):
        step = self._script[self._idx]
        self._idx += 1
        return step

    async def chat_with_tools_stream(self, messages, tools, *, model: str = "scripted", **kwargs):
        """把脚本中的非流式 dict 拆成多个流式 chunk 模拟真流式."""
        step = self._script[self._idx]
        self._idx += 1
        content = step.get("content", "") or ""
        tool_calls = step.get("tool_calls") or []
        usage = step.get("usage") or {}

        for ch in content:
            yield {
                "content_delta": ch,
                "tool_calls": None,
                "finish_reason": None,
                "usage": None,
                "model": model,
            }
        # 真实 provider 行为: tool call 的 name 先到 (此处提前吐 started 信号),
        # arguments 流式生成完后才在 finish chunk 给出完整 tool_calls。
        if tool_calls:
            yield {
                "content_delta": "",
                "tool_calls": None,
                "tool_call_started": [
                    {"id": tc.id, "index": i, "name": tc.name}
                    for i, tc in enumerate(tool_calls)
                ],
                "finish_reason": None,
                "usage": None,
                "model": model,
            }
        yield {
            "content_delta": "",
            "tool_calls": tool_calls if tool_calls else None,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "usage": usage,
            "model": model,
        }


class _AsyncOnlyTool(Tool):
    name = "async_only"
    description = "只实现 arun 的测试工具"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    async def arun(self, args):
        return {"ok": True, "value": args["value"]}


def _make_executor(*tools: Tool) -> ToolExecutor:
    """造一个临时 ToolExecutor + 临时 registry，避免污染全局注册表。"""
    fake = type("FakeRegistry", (), {})()
    by_name = {t.name: t for t in tools}
    fake.get = lambda name: by_name.get(name)
    fake.get_all = lambda: list(tools)
    return ToolExecutor(registry=fake)


def test_react_agent_finishes_without_tools():
    llm = _ScriptedLLM(
        [
            {
                "content": "Hello, world!",
                "tool_calls": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        ]
    )
    agent = ReActAgent(llm, tools=[], max_steps=5)
    result = agent.run("hi")
    assert result.output == "Hello, world!"
    assert result.steps == 1


def test_react_agent_executes_one_tool_then_finishes():
    """第一步要求调 calculator, 第二步给出最终答案."""
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="calculator", arguments={"expression": "2+2"})
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            {
                "content": "答案是 4",
                "tool_calls": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
        ]
    )
    agent = ReActAgent(llm, max_steps=5)
    result = agent.run("2+2 等于几")
    assert "4" in result.output
    assert result.steps == 2
    assert result.usage["prompt_tokens"] == 30
    assert result.usage["completion_tokens"] == 9
    # 验证 messages 中包含 tool 消息
    assert any(m.role == "tool" for m in result.messages)


def test_react_agent_run_executes_async_only_tool():
    """同步 run() 也应走 aexecute，避免 arun-only 工具被误调 run()."""
    tool = _AsyncOnlyTool()
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="async_only", arguments={"value": "ok"})
                ],
                "usage": {},
            },
            {
                "content": "完成",
                "tool_calls": [],
                "usage": {},
            },
        ]
    )
    agent = ReActAgent(
        llm,
        tools=[tool],
        executor=_make_executor(tool),
        max_steps=3,
    )

    result = agent.run("调用异步工具")

    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert tool_msgs
    assert '"value": "ok"' in tool_msgs[0].content
    assert "未实现 run" not in tool_msgs[0].content


def test_react_agent_handles_invalid_tool_gracefully():
    """模型请求不存在的工具, 应该把错误回灌而不是 crash."""
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [ToolCall(id="c1", name="no_such_tool", arguments={})],
                "usage": {},
            },
            {
                "content": "抱歉, 无法完成",
                "tool_calls": [],
                "usage": {},
            },
        ]
    )
    agent = ReActAgent(llm, max_steps=5)
    result = agent.run("...")
    assert "抱歉" in result.output
    # tool 消息应包含 error 内容
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert tool_msgs and "tool error" in tool_msgs[0].content


def test_react_agent_raises_on_max_steps():
    """LLM 一直要工具, 永不结束, 应该到顶抛 AgentMaxStepsError."""
    infinite_loop = [
        {
            "content": "",
            "tool_calls": [ToolCall(id=str(i), name="current_time", arguments={})],
            "usage": {},
        }
        for i in range(10)
    ]
    llm = _ScriptedLLM(infinite_loop)
    agent = ReActAgent(llm, max_steps=3)
    with pytest.raises(AgentMaxStepsError):
        agent.run("无限循环")


def test_react_agent_passes_history():
    from forge.core.types.message import Message

    llm = _ScriptedLLM(
        [
            {"content": "ok", "tool_calls": [], "usage": {}},
        ]
    )
    agent = ReActAgent(llm, tools=[], max_steps=2)
    history = [
        Message(role="user", content="prev question"),
        Message(role="assistant", content="prev answer"),
    ]
    result = agent.run("current", history=history)
    # 历史 + system + 新 user + 新 assistant
    roles = [m.role for m in result.messages]
    assert roles[0] == "system"
    assert "prev question" in result.messages[1].content
    assert result.messages[-1].role == "assistant"


# ── 流式 (真流式) ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_react_agent_stream_emits_per_char_delta():
    """无 tool 调用时, 每个字符一个 delta 事件."""
    llm = _ScriptedLLM([{"content": "Hi!", "tool_calls": [], "usage": {}}])
    agent = ReActAgent(llm, tools=[], max_steps=3)

    events = []
    async for ev in agent.stream("hello"):
        events.append((ev.type, ev.payload))

    delta_events = [p for t, p in events if t == "delta"]
    assert [p["content"] for p in delta_events] == ["H", "i", "!"]

    done = [p for t, p in events if t == "done"]
    assert done and done[0]["content"] == "Hi!"
    assert done[0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_react_agent_stream_tool_call_round_trip():
    """模型先要求 tool, 再给最终答复 - 全程流式."""
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            {
                "content": "答案是 2",
                "tool_calls": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
        ]
    )
    agent = ReActAgent(llm, max_steps=3)

    events: list = []
    async for ev in agent.stream("1+1?"):
        events.append((ev.type, ev.payload))

    types = [t for t, _ in events]
    assert "tool_call" in types and "tool_result" in types
    assert "delta" in types
    assert "done" in types

    deltas = "".join(p["content"] for t, p in events if t == "delta")
    assert "2" in deltas

    done = [p for t, p in events if t == "done"][-1]
    assert done["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_stream_announces_running_before_tool_result():
    """L1: 收到 started 信号即下发 tool_call(running), 早于工具执行结果;

    同一 tool_call id 仅产生一条累计记录 (提前占位 + 补全 arguments 合并)。
    """
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})
                ],
                "usage": {},
            },
            {"content": "好的", "tool_calls": [], "usage": {}},
        ]
    )
    agent = ReActAgent(llm, max_steps=3)

    seq: list = []
    async for ev in agent.stream("1+1?"):
        # tool_call payload 是 accumulated 里的同一引用, 执行后会被原地改写;
        # 真实链路靠 SSE 序列化固化快照, 测试这里手动浅拷贝模拟同样效果。
        if ev.type == "tool_call":
            seq.append((ev.type, {"tool_call": dict(ev.payload["tool_call"])}))
        else:
            seq.append((ev.type, ev.payload))

    types = [t for t, _ in seq]
    first_tc = types.index("tool_call")
    first_res = types.index("tool_result")
    # running 占位必须早于 tool_result (这正是 L1 要填补的空白窗口)
    assert first_tc < first_res
    tc_payload = seq[first_tc][1]["tool_call"]
    assert tc_payload["status"] == "running"
    assert tc_payload["tool_name"] == "calculator"

    # 去重: done 里 c1 只出现一次 (upsert 而非新增第二张卡片)
    done = [p for t, p in seq if t == "done"][-1]
    tool_ids = [r["id"] for r in (done["tool_calls"] or [])]
    assert tool_ids.count("c1") == 1


# ── reasoning_end 统一信号 ──────────────────────────────────
@pytest.mark.asyncio
async def test_stream_emits_reasoning_signals_for_non_thinking_model():
    """非 thinking 模型: reasoning_end 必须在首个 delta 之前."""
    llm = _ScriptedLLM([{"content": "Hi!", "tool_calls": [], "usage": {}}])
    agent = ReActAgent(llm, tools=[], max_steps=3)

    seq: list[tuple[str, dict]] = []
    async for ev in agent.stream("hello"):
        seq.append((ev.type, ev.payload))

    types = [t for t, _ in seq]
    # reasoning_end 必须在首个 delta 之前
    assert "reasoning_end" in types
    end_idx = types.index("reasoning_end")
    first_delta_idx = types.index("delta")
    assert end_idx < first_delta_idx, "reasoning_end 必须先于首个 delta"
    # reasoning_end 携带 reasoning_duration_ms (非 thinking 模型为 0)
    end_payload = seq[end_idx][1]
    assert "reasoning_duration_ms" in end_payload


@pytest.mark.asyncio
async def test_stream_reasoning_end_before_tool_call_when_no_content():
    """整 step 只有 tool_call 没 content: reasoning_end 必须在 tool_call 之前."""
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})
                ],
                "usage": {},
            },
            {"content": "done", "tool_calls": [], "usage": {}},
        ]
    )
    agent = ReActAgent(llm, max_steps=3)

    types: list[str] = []
    async for ev in agent.stream("?"):
        types.append(ev.type)

    # 第一段 reasoning_end 必须在第一个 tool_call 之前
    first_reasoning_end = types.index("reasoning_end")
    first_tool_call = types.index("tool_call")
    assert first_reasoning_end < first_tool_call


@pytest.mark.asyncio
async def test_stream_reasoning_signals_per_step():
    """多 step 流程: 每个 step 都发自己的 reasoning_end."""
    llm = _ScriptedLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})
                ],
                "usage": {},
            },
            {"content": "2", "tool_calls": [], "usage": {}},
        ]
    )
    agent = ReActAgent(llm, max_steps=3)

    types: list[str] = []
    async for ev in agent.stream("?"):
        types.append(ev.type)

    # 至少 2 个 reasoning_end (每个 step 一个)
    assert types.count("reasoning_end") >= 2
