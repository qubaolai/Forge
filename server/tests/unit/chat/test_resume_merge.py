"""Resume 路径的合并 / messages 注入逻辑单测.

不跑真 DB / 真 LLM. 验:
    1. Finalizer._merge_with_prev: 文本追加, tool_calls 累加, usage 相加
    2. Finalizer.finalize 在 prev_state 非空时 -> 合并后写库 / 发事件
    3. Orchestrator._inject_partial_into_messages:
        - 完成态 tool_call -> 转 tool message
        - 未完成态 tool_call (status=running) -> 整体丢弃 (避免 LLM 报错)
        - partial_assistant 插在最末尾 prompt 之前
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from forge.chat.finalizer import TurnFinalizer
from forge.chat.orchestrator import _inject_partial_into_messages
from forge.chat.types import ResumeState, RunResult, TurnContext
from forge.context_mgmt.types import ContextSnapshot, ContextUsage, WindowBudget
from forge.core.content_merge import strip_overlap
from forge.core.types.message import Message


def _snapshot() -> ContextSnapshot:
    """构造最小可用的 ContextSnapshot（finalize 仅透传给被 mock 的 _update_message）。"""
    return ContextSnapshot(
        messages=[],
        budget=WindowBudget(
            context_window=8192, system_budget=0, dialogue_budget=0, tool_result_budget=0,
        ),
        usage=ContextUsage(
            context_window=8192, total_input_tokens=0, max_output_tokens=8192, total_ratio=0.0,
        ),
    )


def _resume_state(
    *,
    content="老内容",
    tool_calls=None,
    reasoning="老思考",
    usage=None,
) -> ResumeState:
    return ResumeState(
        original_user_message="原问题",
        prev_content=content,
        prev_tool_calls=tool_calls or [],
        prev_reasoning_content=reasoning,
        prev_reasoning_duration_ms=500,
        prev_usage=usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        prev_finish_reason="aborted",
        prev_status="aborted",
    )


def _ctx() -> TurnContext:
    return TurnContext(
        user_id="u1",
        user_name="",
        session_id="sess_x",
        assistant_msg_id="msg_a",
        user_msg_id="msg_u",
        current_user_message="continue",
        agent_mode="react",
        is_new_session=False,
        new_title=None,
        trace_id="",
        context_window=8192,
    )


# ---------------------------------------------------------------------------
# _merge_with_prev
# ---------------------------------------------------------------------------
def test_merge_appends_content() -> None:
    prev = _resume_state(content="老A 老B")
    new = RunResult(finish_reason="stop", content=" 新C")
    merged = TurnFinalizer._merge_with_prev(new, prev)
    assert merged.content == "老A 老B 新C"


# ---------------------------------------------------------------------------
# R8: continuation overlap / suffix-prefix overlap strip
# ---------------------------------------------------------------------------
def test_strip_overlap_no_overlap_returns_new_unchanged() -> None:
    stripped, n = strip_overlap("hello", "world")
    assert stripped == "world"
    assert n == 0


def test_strip_overlap_empty_inputs() -> None:
    assert strip_overlap("", "x") == ("x", 0)
    assert strip_overlap("x", "") == ("", 0)
    assert strip_overlap("", "") == ("", 0)


def test_strip_overlap_full_word_overlap() -> None:
    """prev 以 'return' 结束, new 以 'return 42' 开头 -> strip 'return'."""
    stripped, n = strip_overlap("def foo():\n    return", "    return 42\n```")
    assert stripped == " 42\n```"
    assert n == 10  # "    return" = 10 chars


def test_strip_overlap_picks_longest_match() -> None:
    """有多个可能匹配长度时, 取最长的."""
    prev = "hello world"
    new = "world is great"
    stripped, n = strip_overlap(prev, new)
    # 最长重叠 = "world" (5 chars)
    assert stripped == " is great"
    assert n == 5


def test_strip_overlap_respects_max() -> None:
    """超过 max_overlap 不会无限往前找."""
    prev = "x" * 1000
    new = "x" * 1000 + "tail"
    stripped, n = strip_overlap(prev, new, max_overlap=50)
    # 即使可重叠 1000 chars, 也只检测 50
    assert n == 50
    assert stripped == "x" * 950 + "tail"


def test_strip_overlap_code_block_realistic() -> None:
    """实际代码块截断场景."""
    prev = "Here is the function:\n```python\ndef add(a, b):\n    return a"
    new = "    return a + b\n```\nDone."
    stripped, n = strip_overlap(prev, new)
    assert n == 12  # "    return a" = 12 chars
    assert stripped == " + b\n```\nDone."
    # 合并后没有 "    return a" 重复
    merged = prev + stripped
    assert merged == (
        "Here is the function:\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
    )


def test_strip_overlap_no_partial_word_false_positive() -> None:
    """prev 末尾是 'fooba', new 开头是 'foobar...' -> 仍能识别为 'fooba' 的overlap."""
    prev = "let's name it: fooba"
    new = "foobar (the rest)"
    stripped, n = strip_overlap(prev, new)
    # "fooba" 5 chars 是 longest match
    assert n == 5
    assert stripped == "r (the rest)"


def test_merge_strips_overlap_in_resume() -> None:
    """端到端: _merge_with_prev 用 strip 后内容拼接."""
    prev = _resume_state(content="代码:\n```py\ndef f():\n    return")
    new = RunResult(finish_reason="stop", content="    return 42\n```")
    merged = TurnFinalizer._merge_with_prev(new, prev)
    # 期望: prev 不变, new 的 '    return' 重叠被剥离
    assert merged.content == "代码:\n```py\ndef f():\n    return 42\n```"


def test_merge_accumulates_tool_calls() -> None:
    prev = _resume_state(tool_calls=[{"id": "tc1"}])
    new = RunResult(finish_reason="stop", content="", tool_calls=[{"id": "tc2"}])
    merged = TurnFinalizer._merge_with_prev(new, prev)
    assert [tc["id"] for tc in merged.tool_calls] == ["tc1", "tc2"]


def test_merge_sums_usage() -> None:
    prev = _resume_state(usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    new = RunResult(
        finish_reason="stop",
        usage={"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
    )
    merged = TurnFinalizer._merge_with_prev(new, prev)
    assert merged.usage == {
        "prompt_tokens": 120,
        "completion_tokens": 80,
        "total_tokens": 200,
    }


def test_merge_accumulates_reasoning() -> None:
    prev = _resume_state(reasoning="思考A")
    new = RunResult(finish_reason="stop", reasoning_content="思考B", reasoning_duration_ms=300)
    merged = TurnFinalizer._merge_with_prev(new, prev)
    assert merged.reasoning_content == "思考A思考B"
    assert merged.reasoning_duration_ms == 800  # 500 + 300


def test_merge_finish_reason_from_new() -> None:
    """合并后的终态来自本轮 (不是上次)."""
    prev = _resume_state()  # prev_finish_reason="aborted"
    new = RunResult(finish_reason="stop")
    merged = TurnFinalizer._merge_with_prev(new, prev)
    assert merged.finish_reason == "stop"


# ---------------------------------------------------------------------------
# finalize 集成 (prev_state 路径)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_with_prev_state_writes_merged_content() -> None:
    """resume 完整完成 -> done 事件 + 合并后内容写库 + publish."""
    finalizer = TurnFinalizer()
    update = AsyncMock()
    publish = AsyncMock()
    prev = _resume_state(content="老")
    new_result = RunResult(finish_reason="stop", content="新", usage={"total_tokens": 10})

    events: list = []
    with (
        patch.object(TurnFinalizer, "_update_message", update),
        patch.object(TurnFinalizer, "_publish_turn_completed", publish),
    ):
        ev = await finalizer.finalize(_ctx(), new_result, _snapshot(), prev_state=prev)
        if ev:
            events.append(ev)

    assert len(events) == 1
    assert events[0].type == "done"

    # update_message 被传入的 result 应是合并后
    call_args = update.await_args.args
    # _update_message(self, ctx, result, build_meta, status)
    # patched method 不绑定 self, 所以 args[1] 是合并后的 RunResult
    merged_result = call_args[1]
    assert merged_result.content == "老新"
    publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# _inject_partial_into_messages
# ---------------------------------------------------------------------------
def test_inject_appends_partial_before_last_prompt() -> None:
    base = [
        Message(role="system", content="sys"),
        Message(role="user", content="原问题"),
        Message(role="user", content="<current_question>继续</current_question>"),
    ]
    prev = _resume_state(content="部分回答", tool_calls=[])
    out = _inject_partial_into_messages(base, prev)
    # 末尾仍是 resume prompt
    assert out[-1].content == "<current_question>继续</current_question>"
    # 倒数第二是 partial_assistant
    assert out[-2].role == "assistant"
    assert out[-2].content == "部分回答"


def test_inject_completed_tool_call_becomes_tool_message() -> None:
    base = [
        Message(role="system", content="sys"),
        Message(role="user", content="<current_question>继续</current_question>"),
    ]
    prev = _resume_state(
        content="我读了一下",
        tool_calls=[
            {
                "id": "tc1",
                "tool_name": "read_file",
                "arguments": {"p": "a.py"},
                "status": "success",
                "result": "file content",
            }
        ],
    )
    out = _inject_partial_into_messages(base, prev)

    # [system, partial_asst (with tool_calls=[tc1]), tool_msg(tc1), resume_prompt]
    assert len(out) == 4
    assert out[1].role == "assistant"
    assert out[1].tool_calls is not None
    assert len(out[1].tool_calls) == 1
    assert out[1].tool_calls[0].id == "tc1"
    assert out[2].role == "tool"
    assert out[2].tool_call_id == "tc1"
    assert out[2].content == "file content"
    assert out[3].content == "<current_question>继续</current_question>"


def test_inject_drops_unfinished_tool_call() -> None:
    """status=running 的 tool_call 不该出现 (LLM 会因没对应 tool_result 报错)."""
    base = [
        Message(role="system", content="sys"),
        Message(role="user", content="<current_question>继续</current_question>"),
    ]
    prev = _resume_state(
        content="开始读取",
        tool_calls=[
            {"id": "tc1", "tool_name": "x", "arguments": {}, "status": "success", "result": "ok"},
            {"id": "tc2", "tool_name": "y", "arguments": {}, "status": "running"},
        ],
    )
    out = _inject_partial_into_messages(base, prev)
    assistant_msg = out[1]
    assert assistant_msg.tool_calls is not None
    # 只有 tc1 (success), tc2 (running) 被丢
    assert [tc.id for tc in assistant_msg_tool_calls(assistant_msg)] == ["tc1"]

    # tool_results 也只有 tc1 那一条
    tool_msgs = [m for m in out if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"


def assistant_msg_tool_calls(msg):
    return msg.tool_calls or []


def test_inject_no_partial_returns_base_unchanged() -> None:
    base = [
        Message(role="system", content="sys"),
        Message(role="user", content="prompt"),
    ]
    prev = _resume_state(content="", tool_calls=[])
    out = _inject_partial_into_messages(base, prev)
    assert out == base
