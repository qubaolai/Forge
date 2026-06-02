"""chat profile 的 tools_allowed 应被 ContextAssembler 渲染到 system prompt.

阶段 4 起, chat 工具白名单从 agent_profiles 读取, 不再来自
task_execution.chat_tool_allowlist (阶段 8 一并删除).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.agents.profiles import reset_profiles
from forge.chat.assembler import ContextAssembler
from forge.chat.tools import resolve_chat_tools
from forge.config.domains.agent_profiles import (
    AgentProfile,
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
        persistence="chat_db",
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
    tools = [_FakeTool("knowledge_search"), _FakeTool("read_file"), _FakeTool("shell")]

    asm = ContextAssembler()
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.user_name = "用户"
    settings = MagicMock()

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.assembler.get_settings", return_value=settings),
        patch("forge.chat.assembler.fetch_kb_list", return_value=[]),
    ):
        prompt = await asm._render_system_prompt(ctx)  # noqa: SLF001

    assert "knowledge_search" in prompt
    assert "read_file" not in prompt
    assert "shell" not in prompt
