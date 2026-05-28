"""TurnResumer 单测.

不跑真 DB; mock repos 验业务路径.

覆盖:
    1. message 不存在 -> ResumeError(40440)
    2. session 不属于用户 -> ResumeError(40310)
    3. status 不可恢复 (done / error / streaming) -> ResumeError(40901)
    4. parent_id 缺失 / 原 user 消息已删 -> ResumeError(40441)
    5. 正常路径: 返回 TurnContext + ResumeState, msg.status 被改成 streaming
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.chat.resumer import ResumeError, TurnResumer


def _build_fake_db(*, asst, session, parent, agent=None):
    """造 session_factory + repo mock。agent 参数已废弃，保留兼容。"""
    msg_repo = MagicMock()
    sess_repo = MagicMock()

    by_id = {}
    if asst is not None and getattr(asst, "id", None):
        by_id[asst.id] = asst
    if parent is not None and getattr(parent, "id", None):
        by_id[parent.id] = parent

    msg_repo.get_by_id = AsyncMock(side_effect=lambda mid: by_id.get(mid))

    def _update(m, **kw):
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    msg_repo.update = AsyncMock(side_effect=_update)
    sess_repo.get_by_id = AsyncMock(return_value=session)

    fake_db = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.close = AsyncMock()
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__.return_value = fake_db
    ctx_mgr.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx_mgr)
    return factory, msg_repo, sess_repo


def _patches(factory, msg_repo, sess_repo):
    """一组 patch (按 resumer 内部 import 的位置)."""
    return [
        patch("forge.chat.resumer.get_session_factory", return_value=factory),
        patch("forge.chat.resumer.ChatMessageRepository", return_value=msg_repo),
        patch("forge.chat.resumer.ChatSessionRepository", return_value=sess_repo),
    ]


def _asst(status="partial", content="部分文本", tool_calls=None, parent_id="msg_u1"):
    return SimpleNamespace(
        id="msg_a1",
        role="assistant",
        session_id="sess_x",
        content=content,
        status=status,
        parent_id=parent_id,
        tool_calls=tool_calls,
        reasoning_content=None,
        reasoning_duration_ms=None,
        usage={},
        context_meta={"finish_reason": "partial_steps"},
    )


def _parent(id_="msg_u1"):
    return SimpleNamespace(id=id_, role="user", content="原始问题")


def _session(user_id="u1"):
    return SimpleNamespace(id="sess_x", user_id=user_id, agent_id="default")


async def _prepare(asst, session, parent, *, user_id="u1"):
    factory, m, s = _build_fake_db(asst=asst, session=session, parent=parent)
    with ExitStack() as stack:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        ctx, resume = await TurnResumer().prepare(
            user_id=user_id, message_id="msg_a1", trace_id="trace-x"
        )
    return ctx, resume, m


# ---------------------------------------------------------------------------
# 异常路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_message_raises() -> None:
    factory, m, s = _build_fake_db(asst=None, session=None, parent=None)
    m.get_by_id = AsyncMock(return_value=None)
    with ExitStack() as stack, pytest.raises(ResumeError) as exc:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        await TurnResumer().prepare(user_id="u1", message_id="msg_a1", trace_id="")
    assert exc.value.code == "40440"


@pytest.mark.asyncio
async def test_session_mismatch_raises() -> None:
    other = _session(user_id="someone_else")
    factory, m, s = _build_fake_db(asst=_asst(), session=other, parent=_parent())
    with ExitStack() as stack, pytest.raises(ResumeError) as exc:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        await TurnResumer().prepare(user_id="u1", message_id="msg_a1", trace_id="")
    assert exc.value.code == "40310"


@pytest.mark.asyncio
async def test_status_done_rejected() -> None:
    factory, m, s = _build_fake_db(
        asst=_asst(status="done"), session=_session(), parent=_parent()
    )
    with ExitStack() as stack, pytest.raises(ResumeError) as exc:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        await TurnResumer().prepare(user_id="u1", message_id="msg_a1", trace_id="")
    assert exc.value.code == "40901"


@pytest.mark.asyncio
async def test_status_streaming_rejected() -> None:
    """并发 resume 防护."""
    factory, m, s = _build_fake_db(
        asst=_asst(status="streaming"), session=_session(), parent=_parent()
    )
    with ExitStack() as stack, pytest.raises(ResumeError) as exc:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        stack.enter_context(
            patch("forge.chat.resumer._is_message_active_locally", return_value=True)
        )
        await TurnResumer().prepare(user_id="u1", message_id="msg_a1", trace_id="")
    assert exc.value.code == "40902"


@pytest.mark.asyncio
async def test_missing_parent_raises() -> None:
    factory, m, s = _build_fake_db(asst=_asst(parent_id=None), session=_session(), parent=None)
    with ExitStack() as stack, pytest.raises(ResumeError) as exc:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        await TurnResumer().prepare(user_id="u1", message_id="msg_a1", trace_id="")
    assert exc.value.code == "40441"


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resume_happy_path_marks_streaming_and_returns_state() -> None:
    asst = _asst(
        status="aborted",
        content="我开始回答: 1.",
        tool_calls=[
            {
                "id": "tc1",
                "tool_name": "read_file",
                "arguments": {"p": "a.py"},
                "status": "success",
                "result": "...",
            },
            {
                "id": "tc2",
                "tool_name": "read_file",
                "arguments": {"p": "b.py"},
                "status": "running",
            },
        ],
    )
    ctx, resume, msg_repo = await _prepare(asst, _session(), _parent())

    # status 已置回 streaming
    msg_repo.update.assert_awaited_once()
    update_kwargs = msg_repo.update.await_args.kwargs
    assert update_kwargs.get("status") == "streaming"

    # ctx
    assert ctx.assistant_msg_id == "msg_a1"
    assert ctx.user_msg_id == "msg_u1"
    assert ctx.session_id == "sess_x"
    # current_user_message 由模板渲染, 应当包含核心引导短语
    assert "继续完成回答" in ctx.current_user_message
    assert "不要重复" in ctx.current_user_message
    assert ctx.agent_mode == "chat"
    assert ctx.is_new_session is False
    assert ctx.exclude_message_ids == ()

    # resume state: 注意 prev_status 应是 update 前的 "aborted"
    assert resume.original_user_message == "原始问题"
    assert resume.prev_content == "我开始回答: 1."
    assert len(resume.prev_tool_calls) == 2
    assert resume.prev_finish_reason == "partial_steps"
    assert resume.prev_status == "aborted"


@pytest.mark.asyncio
async def test_stale_streaming_can_be_resumed() -> None:
    """遗留 streaming（本地无活跃流）允许接管续写。"""
    asst = _asst(
        status="streaming",
        content="上次中断在这里",
    )
    factory, m, s = _build_fake_db(asst=asst, session=_session(), parent=_parent())
    with ExitStack() as stack:
        for p in _patches(factory, m, s):
            stack.enter_context(p)
        stack.enter_context(
            patch("forge.chat.resumer._is_message_active_locally", return_value=False)
        )
        ctx, resume = await TurnResumer().prepare(
            user_id="u1", message_id="msg_a1", trace_id="trace-x"
        )

    assert ctx.assistant_msg_id == "msg_a1"
    assert resume.prev_status == "aborted"
