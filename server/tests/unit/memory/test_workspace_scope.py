"""memory.scope.workspace_id 链路单测 (S6 P3 M4.2).

验证 summary 在 ContextBuilder 中按 workspace_id 隔离: 同一 session_id 在
workspace_a 下能拿到摘要, 在 workspace_b 下查不到.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from forge.context.base import AgentContextConfig, BuildRequest
from forge.context.builder import CompositeContextBuilder
from forge.llm.token_counter import HeuristicCounter
from forge.memory.base import Fact, FactRecallRequest, Summary


@dataclass
class _FakeRow:
    id: str
    role: str
    content: str


class _EmptyRepo:
    async def load_recent(self, session_id: str, limit: int):  # noqa: ANN001
        return []


class _WorkspaceScopedMemory:
    """只有 workspace_id 命中时才返回 summary."""

    def __init__(self, workspace_id: str, summary: Summary) -> None:
        self._workspace_id = workspace_id
        self._summary = summary

    async def get_summary(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        if workspace_id != self._workspace_id:
            return None
        return self._summary

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        return []


def _request(*, workspace_id: str | None) -> BuildRequest:
    return BuildRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="当前问题",
        agent=AgentContextConfig(
            system_prompt="你是助手",
            context_window=4096,
            history_limit=30,
        ),
        workspace_id=workspace_id,
    )


@pytest.mark.asyncio
async def test_summary_isolated_by_workspace_id() -> None:
    summary = Summary(
        session_id="s1",
        content="workspace A summary",
        covered_until_message_id=None,
        token_count=10,
        updated_at=datetime(2026, 1, 1),
        workspace_id="ws_a",
    )
    memory = _WorkspaceScopedMemory("ws_a", summary)
    builder = CompositeContextBuilder(_EmptyRepo(), memory, HeuristicCounter())

    ws_a = await builder.build(_request(workspace_id="ws_a"))
    ws_b = await builder.build(_request(workspace_id="ws_b"))

    assert "workspace A summary" in ws_a.messages[0].content
    assert ws_a.meta.summary_included is True
    assert "workspace A summary" not in ws_b.messages[0].content
    assert ws_b.meta.summary_included is False
