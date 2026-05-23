from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.chat.assembler import ContextAssembler
from forge.chat.tools import resolve_chat_tools
from forge.tools.base import Tool


class _FakeTool(Tool):
    parameters = {"type": "object"}

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} 工具"

    def run(self, args):
        return "ok"


def test_resolve_chat_tools_filters_to_allowlist() -> None:
    settings = MagicMock()
    settings.task_execution.chat_tool_allowlist = ["knowledge_search"]
    tools = [_FakeTool("knowledge_search"), _FakeTool("read_file"), _FakeTool("shell")]

    with patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools):
        selected = resolve_chat_tools(settings)

    assert [t.name for t in selected] == ["knowledge_search"]


@pytest.mark.asyncio
async def test_chat_system_prompt_only_lists_chat_tools() -> None:
    settings = MagicMock()
    settings.task_execution.chat_tool_allowlist = ["knowledge_search"]
    tools = [_FakeTool("knowledge_search"), _FakeTool("read_file"), _FakeTool("shell")]

    asm = ContextAssembler()
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.user_name = "用户"

    with (
        patch("forge.chat.tools.ToolRegistry.get_all", return_value=tools),
        patch("forge.chat.assembler.get_settings", return_value=settings),
        patch("forge.chat.assembler.fetch_kb_list", return_value=[]),
    ):
        prompt = await asm._render_system_prompt(ctx)  # noqa: SLF001

    assert "knowledge_search" in prompt
    assert "read_file" not in prompt
    assert "shell" not in prompt
