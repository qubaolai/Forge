"""ToolAccessFilter 单测。"""

from __future__ import annotations

from forge.guardrails.tool.access_filter import ToolAccessFilter
from forge.tools.base import Tool


class _SafeTool(Tool):
    name = "read_file"
    description = "safe"
    parameters = {}
    dangerous = False


class _DangerousTool(Tool):
    name = "write_file"
    description = "dangerous"
    parameters = {}
    dangerous = True


def test_tool_access_filter_always_allows() -> None:
    filter_ = ToolAccessFilter(client_type="web")
    assert filter_.check(_SafeTool(), "developer", {}).allow is True
    assert filter_.check(_DangerousTool(), "developer", {}).allow is True
