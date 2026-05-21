from __future__ import annotations

import json
from datetime import UTC, datetime

import aiofiles
import pytest
from config import paths

from forge.orchestration.workflow import ArtifactStore, WorkflowStore


@pytest.mark.asyncio
async def test_workflow_phase_state_legacy_artifact_migrates(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    workflow_id = "wf_legacy"
    now = datetime.now(UTC).isoformat()
    legacy_state = {
        "workflow_id": workflow_id,
        "workspace_path": str(workspace),
        "template_id": "quick_fix",
        "template_name": "Quick Fix",
        "intent": "quick_fix",
        "input_message": "fix",
        "status": "running",
        "current_phase_index": 0,
        "created_at": now,
        "updated_at": now,
        "phases": [
            {
                "id": "p1",
                "role": "developer",
                "task": "do",
                "status": "done",
                "artifact": {
                    "type": "phase_output",
                    "title": "legacy",
                    "summary": "legacy summary",
                    "payload": {"secret": "keep in file"},
                },
            }
        ],
        "metadata": {},
        "owner_user_id": "local",
    }

    state_path = paths.workflow_state_path(workspace, workflow_id)
    async with aiofiles.open(state_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(legacy_state, ensure_ascii=False, indent=2))

    store = WorkflowStore()
    state = await store.load_state(workflow_id, workspace_root=workspace)
    assert state is not None
    phase = state.phases[0]
    assert phase.artifact_id is not None
    assert phase.legacy_artifact is None

    artifact = await ArtifactStore().load(
        phase.artifact_id,
        workflow_id=workflow_id,
        workspace_root=workspace,
    )
    assert artifact is not None
    assert artifact.summary == "legacy summary"

    async with aiofiles.open(state_path, encoding="utf-8") as f:
        rewritten = json.loads(await f.read())
    phase_payload = rewritten["phases"][0]
    assert "artifact_id" in phase_payload
    assert "artifact" not in phase_payload
