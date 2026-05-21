"""ToolAccessFilter 单测 (S6.5 M4)."""

from __future__ import annotations

import pytest

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


def test_tool_access_filter_local_mode_pass_through() -> None:
    filter_ = ToolAccessFilter(deployment_mode="local", client_type="web")
    assert filter_.check(_SafeTool(), "developer", {}).allow is True
    assert filter_.check(_DangerousTool(), "developer", {}).allow is True


def test_tool_access_filter_sandbox_mode_allows_safe_tools() -> None:
    filter_ = ToolAccessFilter(deployment_mode="sandbox", client_type="cli")
    assert filter_.check(_SafeTool(), "developer", {}).allow is True


def test_tool_access_filter_sandbox_mode_raises_not_implemented() -> None:
    filter_ = ToolAccessFilter(deployment_mode="sandbox", client_type="web")
    with pytest.raises(NotImplementedError, match="S6.5 sandbox 策略待实现"):
        filter_.check(_DangerousTool(), "developer", {})
