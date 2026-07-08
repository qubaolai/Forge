"""chat profile 的 tools_allowed 应被 Chat 上下文构建闭包渲染到 system prompt.

阶段 4 起, chat 工具白名单从 agent_profiles 读取, 不再来自
task_execution.chat_tool_allowlist (阶段 8 一并删除).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.profiles import reset_profiles
from forge.chat.orchestrator import build_turn_orchestrator
from forge.chat.tools import resolve_chat_tools
from forge.config.domains.agent_profiles import (
    AgentProfile,
)
from forge.context_mgmt.types import (
    ContextRequest,
    ContextSnapshot,
    ContextUsage,
    WindowBudget,
)
from forge.tools.base import Tool


class _FakeTool(Tool):
    parameters = {"type": "object"}

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} 工具"

    def run(self, args):
        return "ok"


def _install_chat_profile(allowed: list[str]) -> None:
    """直接往 agents.profiles 的全局表里写入测试 profile, 跳过启动校验."""
    from forge.agents import profiles as profiles_mod

    reset_profiles()
    profiles_mod._profiles["chat"] = AgentProfile(
        system_prompt_template="profiles/chat",
        tools_allowed=allowed,
    )
    profiles_mod._loaded = True


def test_resolve_chat_tools_filters_to_profile_allowlist() -> None:
    _install_chat_profile(["knowledge_search"])
    tools = [_FakeTool("knowledge_search"), _FakeTool("read_file"), _FakeTool("shell")]

    with patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools):
        selected = resolve_chat_tools()

    assert [t.name for t in selected] == ["knowledge_search"]


def test_resolve_chat_tools_returns_empty_when_profile_missing() -> None:
    reset_profiles()
    tools = [_FakeTool("knowledge_search")]
    with patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools):
        selected = resolve_chat_tools()
    assert selected == ()


@pytest.mark.asyncio
async def test_chat_system_prompt_only_lists_chat_tools() -> None:
    _install_chat_profile(["knowledge_search"])
    # 探针工具名需避开 system prompt 模板里的静态文本 (read_file/write_file 已被代码产出指引提及)
    tools = [_FakeTool("knowledge_search"), _FakeTool("secret_probe_tool"), _FakeTool("shell")]

    async def fake_build(request):
        return ContextSnapshot(
            messages=[],
            budget=WindowBudget(4096, 0, 0, 0),
            usage=ContextUsage(4096, 0, 4096, 0.0),
            rendered_system_prompt=request.system_prompt_override,
        )

    class _DbContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    builder = MagicMock(build=AsyncMock(side_effect=fake_build))
    settings = MagicMock()
    orchestrator = build_turn_orchestrator()
    request = ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="请在知识库里检索付款条款",
        system_prompt_vars={"user_name": "用户", "selected_kb_ids": ["kb-001"]},
    )

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.orchestrator.get_settings", return_value=settings),
        patch("forge.chat.orchestrator.fetch_selected_kb_list", AsyncMock(return_value=[
            {
                "id": "kb-001",
                "name": "合同库",
                "description": "",
                "document_count": 1,
            }
        ])),
        patch("forge.chat.orchestrator.session_scope", side_effect=lambda: _DbContext()),
        patch("forge.chat.orchestrator.build_context_builder", return_value=builder),
    ):
        snapshot = await orchestrator._context_manager.build(  # noqa: SLF001
            request,
            allow_compaction=False,
        )

    prompt = snapshot.rendered_system_prompt
    assert "knowledge_search" in prompt
    assert "secret_probe_tool" not in prompt
    assert "shell" not in prompt


@pytest.mark.asyncio
async def test_chat_system_prompt_tells_model_to_use_kb_ids() -> None:
    _install_chat_profile(["knowledge_search"])
    tools = [_FakeTool("knowledge_search")]

    async def fake_build(request):
        return ContextSnapshot(
            messages=[],
            budget=WindowBudget(4096, 0, 0, 0),
            usage=ContextUsage(4096, 0, 4096, 0.0),
            rendered_system_prompt=request.system_prompt_override,
        )

    class _DbContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    builder = MagicMock(build=AsyncMock(side_effect=fake_build))
    orchestrator = build_turn_orchestrator()
    request = ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="请查知识库里的付款条款",
        system_prompt_vars={"user_name": "用户", "selected_kb_ids": ["kb-001"]},
    )

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.orchestrator.fetch_selected_kb_list", AsyncMock(return_value=[
            {
                "id": "kb-001",
                "name": "合同库",
                "description": "合同相关资料",
                "document_count": 3,
            }
        ])),
        patch("forge.chat.orchestrator.session_scope", side_effect=lambda: _DbContext()),
        patch("forge.chat.orchestrator.build_context_builder", return_value=builder),
    ):
        snapshot = await orchestrator._context_manager.build(  # noqa: SLF001
            request,
            allow_compaction=False,
        )

    prompt = snapshot.rendered_system_prompt
    assert "合同库 (kb_id=kb-001)" in prompt
    assert "kb_ids" in prompt
    assert "不要只传 kb_names" in prompt
    assert "不要臆造 kb_id" in prompt


@pytest.mark.asyncio
async def test_chat_system_prompt_hides_kb_tool_and_list_without_selected_kb() -> None:
    _install_chat_profile(["knowledge_search"])
    tools = [_FakeTool("knowledge_search")]

    async def fake_build(request):
        return ContextSnapshot(
            messages=[],
            budget=WindowBudget(4096, 0, 0, 0),
            usage=ContextUsage(4096, 0, 4096, 0.0),
            rendered_system_prompt=request.system_prompt_override,
        )

    class _DbContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    builder = MagicMock(build=AsyncMock(side_effect=fake_build))
    fetch_kb = AsyncMock(return_value=[
        {
            "id": "kb-001",
            "name": "合同库",
            "description": "合同相关资料",
            "document_count": 3,
        }
    ])
    orchestrator = build_turn_orchestrator()
    request = ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="请查知识库里的付款条款",
        system_prompt_vars={"user_name": "用户"},
    )

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.orchestrator.fetch_selected_kb_list", fetch_kb),
        patch("forge.chat.orchestrator.session_scope", side_effect=lambda: _DbContext()),
        patch("forge.chat.orchestrator.build_context_builder", return_value=builder),
    ):
        snapshot = await orchestrator._context_manager.build(  # noqa: SLF001
            request,
            allow_compaction=False,
        )

    prompt = snapshot.rendered_system_prompt
    assert "knowledge_search" not in prompt
    assert "合同库" not in prompt
    fetch_kb.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_system_prompt_hides_kb_tool_when_no_accessible_kb() -> None:
    _install_chat_profile(["knowledge_search"])
    tools = [_FakeTool("knowledge_search")]

    async def fake_build(request):
        return ContextSnapshot(
            messages=[],
            budget=WindowBudget(4096, 0, 0, 0),
            usage=ContextUsage(4096, 0, 4096, 0.0),
            rendered_system_prompt=request.system_prompt_override,
        )

    class _DbContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    builder = MagicMock(build=AsyncMock(side_effect=fake_build))
    fetch_kb = AsyncMock(return_value=[])
    orchestrator = build_turn_orchestrator()
    request = ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="请查知识库里的付款条款",
        system_prompt_vars={"user_name": "用户", "selected_kb_ids": ["kb-001"]},
    )

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.orchestrator.fetch_selected_kb_list", fetch_kb),
        patch("forge.chat.orchestrator.session_scope", side_effect=lambda: _DbContext()),
        patch("forge.chat.orchestrator.build_context_builder", return_value=builder),
    ):
        snapshot = await orchestrator._context_manager.build(  # noqa: SLF001
            request,
            allow_compaction=False,
        )

    prompt = snapshot.rendered_system_prompt
    assert "knowledge_search" not in prompt
    assert "本轮用户选择的知识库" not in prompt
    fetch_kb.assert_awaited_once_with("u1", ("kb-001",))


@pytest.mark.asyncio
async def test_chat_build_once_uses_fresh_db_session_each_time() -> None:
    """首次构建与压缩后重建不会跨耗时压缩持有同一个 DB session."""

    async def fake_build(request):
        return ContextSnapshot(
            messages=[],
            budget=WindowBudget(4096, 0, 0, 0),
            usage=ContextUsage(4096, 0, 4096, 0.0),
            rendered_system_prompt=request.system_prompt_override,
        )

    entered_sessions: list[object] = []

    class _DbContext:
        async def __aenter__(self):
            db = object()
            entered_sessions.append(db)
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    builder = MagicMock(build=AsyncMock(side_effect=fake_build))
    orchestrator = build_turn_orchestrator()
    request = ContextRequest(
        user_id="u1",
        session_id="s1",
        current_user_message="hi",
    )

    with (
        patch("forge.chat.orchestrator.fetch_selected_kb_list", AsyncMock(return_value=[])),
        patch("forge.chat.orchestrator.session_scope", side_effect=lambda: _DbContext()),
        patch("forge.chat.orchestrator.build_context_builder", return_value=builder),
    ):
        await orchestrator._context_manager._build_once(request)  # noqa: SLF001
        await orchestrator._context_manager._build_once(request)  # noqa: SLF001

    assert len(entered_sessions) == 2
    assert entered_sessions[0] is not entered_sessions[1]
