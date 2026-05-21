"""SessionRepository 的 JSONL 实现回归测试."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from config import paths

from forge.infrastructure.database.repositories.session_repo import SessionRepository
from forge.infrastructure.session_log import find_session_log_path

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SessionRepository:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    return SessionRepository(AsyncMock())


async def test_create_get_and_list(repo: SessionRepository) -> None:
    s1 = await repo.create(user_id="u1", agent_id="default", title="会话一")
    s2 = await repo.create(user_id="u1", agent_id="agent_a", title="会话二")
    s3 = await repo.create(user_id="u2", agent_id="default", title="其它用户")

    got = await repo.get_by_id(s1.id)
    assert got is not None
    assert got.user_id == "u1"
    assert got.title == "会话一"

    items, total = await repo.list_by_user("u1", page=1, page_size=20)
    assert total == 3
    assert {x.id for x in items} == {s1.id, s2.id, s3.id}


async def test_list_filter_and_paging(repo: SessionRepository) -> None:
    await repo.create(user_id="u1", title="支付改造")
    await repo.create(user_id="u1", title="登录问题")
    await repo.create(user_id="u1", title="支付回归")

    items, total = await repo.list_by_user("u1", page=1, page_size=10, q="支付")
    assert total == 2
    assert all("支付" in x.title for x in items)

    p1, _ = await repo.list_by_user("u1", page=1, page_size=1)
    p2, _ = await repo.list_by_user("u1", page=2, page_size=1)
    assert len(p1) == 1
    assert len(p2) == 1
    assert p1[0].id != p2[0].id


async def test_rename_and_delete(repo: SessionRepository) -> None:
    s = await repo.create(user_id="u1", title="初始标题")
    renamed = await repo.update_title(s, "新标题")
    assert renamed.title == "新标题"

    got = await repo.get_by_id(s.id)
    assert got is not None
    assert got.title == "新标题"

    await repo.delete(s)
    assert await repo.get_by_id(s.id) is None
    items, total = await repo.list_by_user("u1", page=1, page_size=20)
    assert total == 0
    assert items == []


async def test_create_uses_current_workspace_bucket(
    repo: SessionRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws_repo"
    nested = root / "src" / "module"
    nested.mkdir(parents=True)
    paths.workspace_dotdir(root)
    monkeypatch.chdir(nested)

    s = await repo.create(user_id="u1", title="workspace会话")
    p = find_session_log_path(s.id)
    assert p is not None
    assert "__global__" not in p.parts
    assert paths.encode_workspace_path(root) in p.parts


async def test_get_session_works_after_cwd_switch(
    repo: SessionRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    (ws1 / "a").mkdir(parents=True)
    (ws2 / "b").mkdir(parents=True)
    paths.workspace_dotdir(ws1)
    paths.workspace_dotdir(ws2)

    monkeypatch.chdir(ws1 / "a")
    s = await repo.create(user_id="u1", title="ws1会话")

    monkeypatch.chdir(ws2 / "b")
    got = await repo.get_by_id(s.id)
    assert got is not None
    assert got.title == "ws1会话"
