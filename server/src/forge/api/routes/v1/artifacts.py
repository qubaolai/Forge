"""Artifact 查询路由骨架（M3）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from forge.adaptive.models import ArtifactKind
from forge.adaptive.store import AdaptiveRunStore
from forge.api.dependencies import CurrentUser
from forge.core.exceptions import NotFound
from forge.core.response import success

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _resolve_workspace_path(raw_workspace_path: str | None) -> str:
    text = (raw_workspace_path or "").strip()
    if not text:
        return str(Path.cwd())
    return str(Path(text).expanduser().resolve())


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str, user: CurrentUser, workspace_path: str | None = None):
    """查询单个 artifact（跨 run）。仅返回当前用户拥有 run 下的 artifact。"""
    store = AdaptiveRunStore(workspace_path=_resolve_workspace_path(workspace_path))
    owner_filter = None if getattr(user, "role", "") in ("owner", "admin") else user.id
    artifact = await store.find_artifact(artifact_id, owner_user_id=owner_filter)
    if artifact is None:
        raise NotFound("artifact 不存在", code=40460)
    return success(artifact.to_dict())


@router.get("")
async def list_artifacts(
    user: CurrentUser,
    workspace_path: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    kind: ArtifactKind | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """按条件查询 artifact。"""
    store = AdaptiveRunStore(workspace_path=_resolve_workspace_path(workspace_path))
    owner_filter = None if getattr(user, "role", "") in ("owner", "admin") else user.id
    items = await store.list_artifacts_across_runs(
        run_id=run_id,
        task_id=task_id,
        kind=kind,
        limit=limit,
        owner_user_id=owner_filter,
    )
    payload = [item.to_dict() for item in items]
    return success({"items": payload, "total": len(payload)})
