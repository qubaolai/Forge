from __future__ import annotations

import json

import pytest

from forge.core.types.message import ToolCall
from forge.tools.base import Tool
from forge.tools.executor import ToolExecutor


class _HugeTool(Tool):
    name = "huge_tool"
    description = "return a large payload"
    parameters = {"type": "object", "properties": {}}

    def run(self, args):
        return {"ok": True, "text": "x" * 50_000}


class _BoomTool(Tool):
    name = "boom_tool"
    description = "raise"
    parameters = {"type": "object", "properties": {}}

    def run(self, args):
        raise RuntimeError("boom")


def _executor(*tools: Tool) -> ToolExecutor:
    fake = type("FakeRegistry", (), {})()
    by_name = {t.name: t for t in tools}
    fake.get = lambda name: by_name.get(name)
    fake.get_all = lambda: list(tools)
    return ToolExecutor(registry=fake)


@pytest.mark.asyncio
async def test_large_tool_result_is_truncated_as_valid_json() -> None:
    executor = _executor(_HugeTool())

    msg = await executor.aexecute(ToolCall(id="c1", name="huge_tool", arguments={}))

    data = json.loads(msg.content)
    assert data["ok"] is True
    assert data["_forge_tool_result_truncated"] is True
    assert data["_forge_original_chars"] > len(msg.content)
    assert len(msg.content) <= 22_000
    assert msg.metadata["truncated"] is True
    assert msg.metadata["result_chars_original"] > msg.metadata["result_chars_returned"]


@pytest.mark.asyncio
async def test_tool_runtime_error_sets_error_status_metadata() -> None:
    executor = _executor(_BoomTool())

    msg = await executor.aexecute(ToolCall(id="c1", name="boom_tool", arguments={}))

    assert "[tool error] boom" in msg.content
    assert msg.metadata["tool_status"] == "error"
    assert msg.metadata["truncated"] is False
