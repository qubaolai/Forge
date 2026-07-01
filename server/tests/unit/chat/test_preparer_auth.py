"""TurnPreparer 权限校验测试."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.chat.preparer import TurnPreparationError, TurnPreparer


def _build_fake_db(*, session):
    """构造 session_factory + repo mock。"""
    msg_repo = MagicMock()
    sess_repo = MagicMock()

    msg_repo.count_by_session = AsyncMock(return_value=1)
    msg_repo.add = AsyncMock()
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
    return [
        patch("forge.chat.preparer.session_scope", side_effect=factory),
        patch("forge.chat.preparer.ChatMessageRepository", return_value=msg_repo),
        patch("forge.chat.preparer.ChatSessionRepository", return_value=sess_repo),
    ]


@pytest.mark.asyncio
async def test_existing_session_owner_mismatch_rejected() -> None:
    """已有会话但 owner 不匹配时，应在写入前直接拒绝。"""
    other_user_session = SimpleNamespace(
        id="sess_x",
        user_id="someone_else",
        title="新会话",
    )
    factory, msg_repo, sess_repo = _build_fake_db(session=other_user_session)

    with ExitStack() as stack, pytest.raises(TurnPreparationError) as exc:
        for p in _patches(factory, msg_repo, sess_repo):
            stack.enter_context(p)
        await TurnPreparer().prepare(
            user_id="u1",
            user_name="tester",
            session_id="sess_x",
            message="你好",
            trace_id="trace-x",
            model_options=None,
        )

    assert exc.value.code == "40310"
    msg_repo.count_by_session.assert_not_called()
    msg_repo.add.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_accessible_kb_ids_keeps_only_authorized_ids(monkeypatch) -> None:
    from forge.chat import preparer as preparer_mod

    class Repo:
        def __init__(self, db) -> None:
            _ = db

        async def find_accessible_by_ids(self, ids, user_id):
            assert ids == ["kb1", "kb2", "kb3"]
            assert user_id == "u1"
            return [SimpleNamespace(id="kb1"), SimpleNamespace(id="kb3")]

    monkeypatch.setattr(
        "forge.infrastructure.database.repositories.knowledge_base_repo.KnowledgeBaseRepository",
        Repo,
    )

    result = await preparer_mod._resolve_accessible_kb_ids(
        object(),
        ["kb1", "kb2", "kb3"],
        "u1",
    )

    assert result == ("kb1", "kb3")
