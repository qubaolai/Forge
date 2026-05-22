"""Integrator 合并与冲突测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.adaptive.integrator import Integrator
from forge.adaptive.models import AdaptiveRun, Artifact, ArtifactKind
from forge.adaptive.store import AdaptiveRunStore

pytestmark = pytest.mark.asyncio


async def test_integrator_merge_without_conflict(tmp_path: Path) -> None:
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_int_ok", workspace_path=str(tmp_path), goal="g")
    await store.save_run(run)

    patch = Artifact(
        artifact_id="art_patch_1",
        run_id=run.run_id,
        task_id="t1",
        kind=ArtifactKind.PATCH_SET,
        payload={"task_id": "t1", "changed_files": ["server/a.py"], "diff": ""},
    )
    await store.save_artifact(patch)
    run.artifact_ids.append(patch.artifact_id)
    await store.save_run(run)

    result = await Integrator(store=store).merge(run=run)
    assert result.merged is True
    assert result.conflict is False
    assert result.report_artifact.kind == ArtifactKind.INTEGRATION_REPORT
    artifacts = await store.list_artifacts(run.run_id)
    assert any(item.kind == ArtifactKind.INTEGRATION_REPORT for item in artifacts)


async def test_integrator_empty_diff_skips_apply(tmp_path: Path) -> None:
    """N18: 单个 changed_files 但 diff 为空时，应当走 merged 路径而非 apply 出错。

    防止 fallback executor 给出 ``{"changed_files": ["foo"], "diff": ""}``
    占位 patch 时 integrator 误把空 diff 投给 git apply。
    """
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_int_empty", workspace_path=str(tmp_path), goal="g")
    await store.save_run(run)

    patch = Artifact(
        artifact_id="art_patch_empty",
        run_id=run.run_id,
        task_id="t1",
        kind=ArtifactKind.PATCH_SET,
        payload={"task_id": "t1", "changed_files": ["server/a.py"], "diff": ""},
    )
    await store.save_artifact(patch)
    run.artifact_ids.append(patch.artifact_id)
    await store.save_run(run)

    result = await Integrator(store=store).merge(run=run)
    # 空 diff 不应触发 apply_conflict
    assert result.merged is True
    assert result.conflict is False
    assert result.reason == "ok"


async def test_integrator_merge_text_conflict(tmp_path: Path) -> None:
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_int_conflict", workspace_path=str(tmp_path), goal="g")
    await store.save_run(run)

    patch_a = Artifact(
        artifact_id="art_patch_a",
        run_id=run.run_id,
        task_id="t1",
        kind=ArtifactKind.PATCH_SET,
        payload={"task_id": "t1", "changed_files": ["server/a.py"], "diff": ""},
    )
    patch_b = Artifact(
        artifact_id="art_patch_b",
        run_id=run.run_id,
        task_id="t2",
        kind=ArtifactKind.PATCH_SET,
        payload={"task_id": "t2", "changed_files": ["server/a.py"], "diff": ""},
    )
    await store.save_artifact(patch_a)
    await store.save_artifact(patch_b)
    run.artifact_ids.extend([patch_a.artifact_id, patch_b.artifact_id])
    await store.save_run(run)

    result = await Integrator(store=store).merge(run=run)
    assert result.merged is False
    assert result.conflict is True
    assert result.conflict_artifact is not None
    assert result.conflict_artifact.kind == ArtifactKind.CONFLICT_REPORT
    artifacts = await store.list_artifacts(run.run_id)
    assert any(item.kind == ArtifactKind.CONFLICT_REPORT for item in artifacts)
