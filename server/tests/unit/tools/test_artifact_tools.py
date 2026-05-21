from __future__ import annotations

import pytest

import forge.tools.builtin  # noqa: F401
from forge.core.types.errors import ToolValidationError
from forge.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_create_get_search_artifact_tools_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    create_tool = ToolRegistry.get("create_artifact")
    get_tool = ToolRegistry.get("get_artifact")
    search_tool = ToolRegistry.get("search_artifact")
    assert create_tool is not None and get_tool is not None and search_tool is not None

    created = await create_tool.arun(
        {
            "workflow_id": "wf_roundtrip",
            "phase_id": "p1",
            "created_by_role": "developer",
            "type": "code_change",
            "title": "Patch A",
            "summary": "更新一个函数",
            "payload": {"diff": "..."},  # 故意保留详情给 get_artifact 验证
            "workspace_root": str(workspace),
        }
    )
    assert created["ok"] is True
    artifact_id = created["artifact_id"]

    searched = await search_tool.arun(
        {"workflow_id": "wf_roundtrip", "limit": 20, "workspace_root": str(workspace)}
    )
    assert searched["ok"] is True
    assert any(item["artifact_id"] == artifact_id for item in searched["items"])

    got = await get_tool.arun(
        {
            "artifact_id": artifact_id,
            "workflow_id": "wf_roundtrip",
            "workspace_root": str(workspace),
        }
    )
    assert got["ok"] is True
    assert got["artifact"]["payload"] == {"diff": "..."}


@pytest.mark.asyncio
async def test_search_artifact_filters_by_type_and_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    create_tool = ToolRegistry.get("create_artifact")
    search_tool = ToolRegistry.get("search_artifact")
    assert create_tool is not None and search_tool is not None

    await create_tool.arun(
        {
            "workflow_id": "wf_a",
            "phase_id": "p1",
            "created_by_role": "developer",
            "type": "code_change",
            "title": "A1",
            "summary": "code A1",
            "payload": {"k": "a1"},
            "workspace_root": str(workspace),
        }
    )
    await create_tool.arun(
        {
            "workflow_id": "wf_a",
            "phase_id": "p2",
            "created_by_role": "qa",
            "type": "test_report",
            "title": "A2",
            "summary": "test A2",
            "payload": {"k": "a2"},
            "workspace_root": str(workspace),
        }
    )
    await create_tool.arun(
        {
            "workflow_id": "wf_b",
            "phase_id": "p1",
            "created_by_role": "developer",
            "type": "code_change",
            "title": "B1",
            "summary": "code B1",
            "payload": {"k": "b1"},
            "workspace_root": str(workspace),
        }
    )

    filtered = await search_tool.arun(
        {
            "workflow_id": "wf_a",
            "type": "test_report",
            "workspace_root": str(workspace),
        }
    )
    assert filtered["ok"] is True
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["workflow_id"] == "wf_a"
    assert filtered["items"][0]["type"] == "test_report"


@pytest.mark.asyncio
async def test_artifact_tools_blocked_outside_workflow_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    create_tool = ToolRegistry.get("create_artifact")
    assert create_tool is not None

    with pytest.raises(ToolValidationError):
        await create_tool.arun(
            {
                "type": "phase_output",
                "title": "x",
                "summary": "x",
                "payload": {},
            }
        )
