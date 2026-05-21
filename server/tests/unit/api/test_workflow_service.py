"""WorkflowService tests — skipped: workflow system replaced by adaptive/."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(reason="workflow system deprecated, replaced by adaptive/")

from forge.api.services.workflow_service import (
    WorkflowService,
    reset_workflow_service,
)
from forge.core.exceptions import BadRequest, Forbidden, NotFound


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_workflow_service()
    yield
    reset_workflow_service()


@pytest.mark.asyncio
async def test_workflow_service_start_and_wait_until_complete(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    svc = WorkflowService()
    started = await svc.start(
        message="请跑回归测试",
        template_id=None,
        workspace_path=str(ws),
        pause_after_phase=False,
        metadata={"source": "test"},
        owner_user_id="local",
    )
    # async start: 立即返回 running, 后台 task 继续跑
    assert started["status"] in {"running", "completed"}
    assert started["owner_user_id"] == "local"

    # 等终态 — 后台 task 跑完 (regression_test 模板)
    final = await svc.orchestrator.wait_for(started["workflow_id"], timeout=10.0)
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_workflow_service_invalid_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    svc = WorkflowService()

    with pytest.raises(BadRequest):
        await svc.start(
            message="x",
            template_id=None,
            workspace_path=str(tmp_path / "not-exists"),
            pause_after_phase=False,
            metadata=None,
        )


@pytest.mark.asyncio
async def test_workflow_service_not_found():
    svc = WorkflowService()
    with pytest.raises(NotFound):
        await svc.get("wf_not_exists")


@pytest.mark.asyncio
async def test_workflow_service_owner_isolation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    svc = WorkflowService()

    started = await svc.start(
        message="x",
        template_id=None,
        workspace_path=str(ws),
        pause_after_phase=True,
        metadata=None,
        owner_user_id="alice",
    )

    with pytest.raises(Forbidden):
        await svc.get(started["workflow_id"], requester_user_id="bob")
