"""MessageRepository 的 JSONL 落地回归测试."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from config import paths

from forge.infrastructure.database.repositories.message_repo import (
    MessageRepository,
)
from forge.infrastructure.session_log import find_session_log_path

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MessageRepository:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    return MessageRepository(AsyncMock())


async def test_add_get_update_and_load_recent(repo: MessageRepository) -> None:
    u = await repo.add(session_id="sess_1", role="user", content="你好", status="done")
    a = await repo.add(
        session_id="sess_1",
        role="assistant",
        content="处理中",
        status="streaming",
        parent_id=u.id,
    )

    got = await repo.get_by_id(a.id)
    assert got is not None
    assert got.status == "streaming"
    assert got.parent_id == u.id

    await repo.update(a, content="完成", status="done", usage={"total_tokens": 10})
    got2 = await repo.get_by_id(a.id)
    assert got2 is not None
    assert got2.content == "完成"
    assert got2.status == "done"
    assert got2.usage == {"total_tokens": 10}

    recent = await repo.load_recent("sess_1", limit=10)
    assert [m.id for m in recent] == [u.id, a.id]


async def test_list_and_counts(repo: MessageRepository) -> None:
    for i in range(5):
        await repo.add(session_id="sess_x", role="user", content=f"m{i}", status="done")

    items, total = await repo.list_by_session("sess_x", page=1, page_size=2)
    assert total == 5
    assert len(items) == 2

    assert await repo.count_by_session("sess_x") == 5
    batch = await repo.count_by_sessions(["sess_x", "sess_none"])
    assert batch["sess_x"] == 5
    assert batch["sess_none"] == 0

    latest = await repo.latest_at_by_sessions(["sess_x"])
    assert "sess_x" in latest


async def test_delete_by_id_hides_message(repo: MessageRepository) -> None:
    msg = await repo.add(session_id="sess_del", role="assistant", content="x", status="done")
    ok = await repo.delete_by_id(msg.id)
    assert ok is True

    assert await repo.get_by_id(msg.id) is None
    items, total = await repo.list_by_session("sess_del", page=1, page_size=20)
    assert total == 0
    assert items == []


async def test_add_uses_workspace_bucket_for_new_session(
    repo: MessageRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ws = tmp_path / "wsa"
    (ws / "deep").mkdir(parents=True)
    paths.workspace_dotdir(ws)
    monkeypatch.chdir(ws / "deep")

    msg = await repo.add(session_id="sess_ws_msg", role="user", content="hello", status="done")
    assert msg.session_id == "sess_ws_msg"

    p = find_session_log_path("sess_ws_msg")
    assert p is not None
    assert "__global__" not in p.parts
    assert paths.encode_workspace_path(ws) in p.parts
