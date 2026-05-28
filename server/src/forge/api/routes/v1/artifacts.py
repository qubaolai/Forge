"""Artifact 查询路由 (基于通用 RunStore).

支持:
    GET  /v1/artifacts/{artifact_id}                  跨 run 查 artifact (workspace 内)
    GET  /v1/artifacts?run_id=...                     列出 run 下的 artifacts

权限: 跨 run 查找时校验 owner_user_id (非 owner 不返回).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from forge.api.dependencies import AuthenticatedUser
from forge.core.exceptions import BadRequest, NotFound
from forge.core.response import success
from forge.infrastructure.run_store import RunStore

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _resolve_workspace_path(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        raise BadRequest("workspace_path 必填", code=40051)
    return str(Path(text).expanduser().resolve())


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="RunStore 根路径"),
    run_id: str | None = Query(default=None, description="可选: 精准定位 run"),
):
    """查询单个 artifact (跨 run). 非 owner 也非 admin 不能看."""
    store = RunStore(workspace_path=_resolve_workspace_path(workspace_path))
    owner_filter = (
        None if getattr(user, "role", "") in ("owner", "admin") else user.user_id
    )
    if run_id:
        item = await store.load_artifact(run_id, artifact_id)
        if item is not None and owner_filter is not None:
            run = await store.load_run(item.run_id)
            if run is None or run.owner_user_id != owner_filter:
                item = None
    else:
        item = await store.find_artifact(artifact_id, owner_user_id=owner_filter)
    if item is None:
        raise NotFound("artifact 不存在", code=40460)
    return success(item.to_dict())


@router.get("")
async def list_artifacts(
    user: AuthenticatedUser,
    workspace_path: str = Query(..., description="RunStore 根路径"),
    run_id: str = Query(..., description="run_id 必填 (避免跨 run 扫描压力)"),
    kind: str | None = None,
    task_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """列出某个 run 下的 artifacts."""
    store = RunStore(workspace_path=_resolve_workspace_path(workspace_path))
    record = await store.load_run(run_id)
    if record is None:
        raise NotFound("run 不存在", code=40450)
    if record.owner_user_id != user.user_id and getattr(user, "role", "") not in (
        "owner",
        "admin",
    ):
        raise NotFound("artifact 不存在", code=40460)
    items = await store.list_artifacts(run_id, kind=kind, task_id=task_id)
    truncated = items[:limit]
    return success({
        "items": [item.to_dict() for item in truncated],
        "total": len(truncated),
    })


__all__ = ["router"]
