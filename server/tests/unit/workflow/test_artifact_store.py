from __future__ import annotations

import asyncio

import pytest

from forge.orchestration.workflow import Artifact, ArtifactStore, ArtifactType


@pytest.mark.asyncio
async def test_artifact_store_atomic_write_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    store = ArtifactStore()
    art1 = Artifact(
        id="art_1",
        workflow_id="wf_1",
        phase_id="p1",
        type=ArtifactType.PHASE_OUTPUT,
        title="t1",
        summary="s1",
        payload={"k": 1},
        created_by_role="developer",
    )
    art2 = Artifact(
        id="art_2",
        workflow_id="wf_1",
        phase_id="p2",
        type=ArtifactType.CODE_CHANGE,
        title="t2",
        summary="s2",
        payload={"k": 2},
        created_by_role="qa",
    )

    await asyncio.gather(
        store.save(art1, workspace_root=workspace),
        store.save(art2, workspace_root=workspace),
    )

    got1 = await store.load("art_1", workflow_id="wf_1", workspace_root=workspace)
    got2 = await store.load("art_2", workflow_id="wf_1", workspace_root=workspace)
    assert got1 == art1
    assert got2 == art2

    listed = await store.list_by_workflow("wf_1", workspace_root=workspace)
    assert {x.id for x in listed} == {"art_1", "art_2"}
