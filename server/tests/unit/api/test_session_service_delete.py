"""SessionService.delete 级联清理摘要单测 (P0-B).

覆盖:
    1. 删除 session 时 SummaryStore.delete 被调用 (级联清理)
    2. 摘要清理失败 (抛错) 不阻断 session 删除主流程 (best-effort)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.api.services.session_service import SessionService
from forge.infrastructure.storage.data_protocols import SessionView


def _session(session_id: str = "9001") -> SessionView:
    return SessionView(
        id=session_id,
        user_id="1001",
        agent_id="",
        title="t",
        created_at=datetime(2026, 6, 1),
        updated_at=datetime(2026, 6, 1),
    )


def _service() -> SessionService:
    svc = SessionService(MagicMock())
    svc.session_repo = MagicMock()
    svc.session_repo.delete = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_delete_cascades_summary_cleanup() -> None:
    svc = _service()
    delete_mock = AsyncMock()

    class _FakeStore:
        def __init__(self, factory) -> None:
            pass

        async def delete(self, session_id):
            await delete_mock(session_id)

    with (
        patch("forge.memory.summary.store.SummaryStore", _FakeStore),
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=MagicMock(),
        ),
    ):
        await svc.delete(_session("9001"))

    svc.session_repo.delete.assert_awaited_once()
    delete_mock.assert_awaited_once_with("9001")


@pytest.mark.asyncio
async def test_summary_cleanup_failure_does_not_block_delete() -> None:
    svc = _service()

    class _BrokenStore:
        def __init__(self, factory) -> None:
            pass

        async def delete(self, session_id):
            raise RuntimeError("db down")

    with (
        patch("forge.memory.summary.store.SummaryStore", _BrokenStore),
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=MagicMock(),
        ),
    ):
        await svc.delete(_session())  # 不抛

    svc.session_repo.delete.assert_awaited_once()
