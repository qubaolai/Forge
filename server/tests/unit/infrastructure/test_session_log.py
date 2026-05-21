"""SessionLog 单元测试.

覆盖:
- init + get_meta (含 datetime 往返)
- append_message 写入与读取
- supersede 语义 (同 id 后写覆盖前写)
- statuses 过滤 / load_recent 默认取 done
- tail_messages 边界 + 顺序保留
- get_message 返回最新版本
- count_messages 与 load_messages 一致
- session_log_for factory: 有 workspace / 全局兜底
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from forge.infrastructure.session_log import (
    MessageRecord,
    SessionLog,
    SessionMeta,
    find_session_log_path,
    session_log_for,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def log(tmp_path: Path) -> SessionLog:
    return SessionLog(tmp_path / "sess_test.jsonl")


# ────────────────────── meta ──────────────────────


async def test_init_and_get_meta_roundtrip(log: SessionLog) -> None:
    meta = SessionMeta(
        session_id="sess_1",
        workspace_path="/Users/almond/code/ec",
        title="加评论功能",
        agent_id="developer",
        tags=["feature"],
        created_at=datetime(2026, 5, 20, 10, 32, 11, tzinfo=UTC),
    )
    await log.init(meta)

    loaded = await log.get_meta()
    assert loaded is not None
    assert loaded.session_id == "sess_1"
    assert loaded.title == "加评论功能"
    assert loaded.workspace_path == "/Users/almond/code/ec"
    assert loaded.tags == ["feature"]
    assert loaded.created_at == datetime(2026, 5, 20, 10, 32, 11, tzinfo=UTC)


async def test_get_meta_missing_returns_none(log: SessionLog) -> None:
    assert await log.get_meta() is None


async def test_exists(log: SessionLog) -> None:
    assert not await log.exists()
    await log.init(SessionMeta(session_id="x"))
    assert await log.exists()


async def test_save_meta_overrides_latest_snapshot(log: SessionLog) -> None:
    await log.init(SessionMeta(session_id="sess_1", user_id="u1", title="旧标题", agent_id="a1"))
    await log.save_meta(
        SessionMeta(session_id="sess_1", user_id="u1", title="新标题", agent_id="a2")
    )
    meta = await log.get_meta()
    assert meta is not None
    assert meta.title == "新标题"
    assert meta.agent_id == "a2"
    assert meta.user_id == "u1"


# ────────────────────── append + load ──────────────────────


async def test_append_and_load_messages(log: SessionLog) -> None:
    await log.init(SessionMeta(session_id="sess_1"))
    await log.append_message(MessageRecord(id="msg_1", role="user", content="你好"))
    await log.append_message(MessageRecord(id="msg_2", role="assistant", content="hi"))

    msgs = await log.load_messages()
    assert [m.id for m in msgs] == ["msg_1", "msg_2"]
    assert msgs[0].content == "你好"
    assert msgs[1].role == "assistant"


async def test_load_messages_preserves_order_first_seen(log: SessionLog) -> None:
    """同 id 多次写, order 按首次出现; 内容取最后一次."""
    await log.append_message(MessageRecord(id="a", role="user", content="v1"))
    await log.append_message(MessageRecord(id="b", role="user", content="v1"))
    await log.append_message(MessageRecord(id="a", role="user", content="v2"))

    msgs = await log.load_messages()
    assert [m.id for m in msgs] == ["a", "b"]
    assert msgs[0].content == "v2"  # supersede


async def test_supersede_status_transition(log: SessionLog) -> None:
    """典型 streaming 场景: 先写 streaming, 后写 done; load 只看到 done."""
    await log.append_message(
        MessageRecord(id="msg_x", role="assistant", content="partial", status="streaming")
    )
    await log.append_message(
        MessageRecord(id="msg_x", role="assistant", content="完整内容", status="done")
    )

    msgs = await log.load_messages()
    assert len(msgs) == 1
    assert msgs[0].status == "done"
    assert msgs[0].content == "完整内容"


# ────────────────────── 过滤 ──────────────────────


async def test_load_messages_filter_by_statuses(log: SessionLog) -> None:
    await log.append_message(MessageRecord(id="1", role="user", content="q", status="done"))
    await log.append_message(MessageRecord(id="2", role="assistant", content="ans", status="error"))
    await log.append_message(MessageRecord(id="3", role="assistant", content="ok", status="done"))

    done_only = await log.load_messages(statuses=("done",))
    assert [m.id for m in done_only] == ["1", "3"]


async def test_load_recent_only_done_by_default(log: SessionLog) -> None:
    """load_recent 默认 status='done' (与原 MessageRepository 对齐)."""
    await log.append_message(MessageRecord(id="1", role="user", content="a", status="done"))
    await log.append_message(MessageRecord(id="2", role="assistant", content="x", status="error"))
    await log.append_message(MessageRecord(id="3", role="user", content="b", status="done"))

    recent = await log.load_recent(limit=10)
    assert [m.id for m in recent] == ["1", "3"]


async def test_load_recent_limit(log: SessionLog) -> None:
    for i in range(20):
        await log.append_message(
            MessageRecord(id=f"m_{i}", role="user", content=str(i), status="done")
        )
    recent = await log.load_recent(limit=5)
    assert [m.id for m in recent] == ["m_15", "m_16", "m_17", "m_18", "m_19"]


# ────────────────────── tail / get / count ──────────────────────


async def test_tail_messages(log: SessionLog) -> None:
    for i in range(7):
        await log.append_message(MessageRecord(id=f"m_{i}", role="user", content=str(i)))
    last3 = await log.tail_messages(3)
    assert [m.id for m in last3] == ["m_4", "m_5", "m_6"]


async def test_tail_messages_zero_returns_empty(log: SessionLog) -> None:
    await log.append_message(MessageRecord(id="x", role="user"))
    assert await log.tail_messages(0) == []
    assert await log.tail_messages(-1) == []


async def test_get_message_returns_latest(log: SessionLog) -> None:
    await log.append_message(MessageRecord(id="m", role="user", content="v1"))
    await log.append_message(MessageRecord(id="m", role="user", content="v2"))
    await log.append_message(MessageRecord(id="m", role="user", content="v3"))

    got = await log.get_message("m")
    assert got is not None
    assert got.content == "v3"


async def test_get_message_missing(log: SessionLog) -> None:
    assert await log.get_message("nope") is None


async def test_count_messages_dedups(log: SessionLog) -> None:
    await log.append_message(MessageRecord(id="a", role="user"))
    await log.append_message(MessageRecord(id="b", role="user"))
    await log.append_message(MessageRecord(id="a", role="user", content="updated"))

    assert await log.count_messages() == 2  # 去重后


# ────────────────────── 复杂字段 ──────────────────────


async def test_message_with_tool_calls_and_usage(log: SessionLog) -> None:
    tool_calls = [{"id": "call_1", "name": "read_file", "args": {"path": "a.py"}}]
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    citations = [{"chunk_id": "c1", "score": 0.9}]
    context_meta = {"used_history": 5, "dropped": 0, "degraded": []}

    await log.append_message(
        MessageRecord(
            id="msg_full",
            role="assistant",
            content="result",
            tool_calls=tool_calls,
            usage=usage,
            citations=citations,
            context_meta=context_meta,
            reasoning_content="thinking step 1",
            reasoning_duration_ms=420,
        )
    )

    got = await log.get_message("msg_full")
    assert got is not None
    assert got.tool_calls == tool_calls
    assert got.usage == usage
    assert got.citations == citations
    assert got.context_meta == context_meta
    assert got.reasoning_content == "thinking step 1"
    assert got.reasoning_duration_ms == 420


# ────────────────────── factory ──────────────────────


async def test_session_log_for_with_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ASSISTANT_HOME", str(home))

    workspace = tmp_path / "code" / "ec"
    workspace.mkdir(parents=True)

    log = session_log_for("sess_w1", workspace_root=workspace)
    await log.init(SessionMeta(session_id="sess_w1"))

    # 路径应在 ~/.assistant/projects/<encoded>/sessions/
    assert "projects" in log.path.parts
    assert log.path.name == "sess_w1.jsonl"
    assert log.path.exists()


async def test_session_log_for_global_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ASSISTANT_HOME", str(home))

    log = session_log_for("sess_g1", workspace_root=None)
    await log.init(SessionMeta(session_id="sess_g1"))

    # 全局兜底走 __global__
    assert "__global__" in log.path.parts
    assert log.path.exists()


async def test_find_session_log_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("ASSISTANT_HOME", str(home))
    workspace = tmp_path / "code" / "ec"
    workspace.mkdir(parents=True)

    log = session_log_for("sess_find_1", workspace_root=workspace)
    await log.init(SessionMeta(session_id="sess_find_1"))

    found = find_session_log_path("sess_find_1")
    assert found is not None
    assert found == log.path
