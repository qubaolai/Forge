"""Workflow artifact 文件化存储."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiofiles
from forge.config import paths

from forge.infrastructure.jsonl import atomic_write_text

from .artifact import Artifact, ArtifactType

logger = logging.getLogger(__name__)


class ArtifactStore:
    """artifact 落盘存储包装.

    目录结构:
        <workflow_run_dir>/artifacts/{artifact_id}.json
    """

    async def save(self, artifact: Artifact, *, workspace_root: Path | None = None) -> Path:
        if workspace_root is not None:
            path = self._artifact_path(
                workflow_id=artifact.workflow_id,
                artifact_id=artifact.id,
                workspace_root=workspace_root,
            )
        else:
            run_dir = paths.find_workflow_run_dir(artifact.workflow_id)
            if run_dir is None:
                raise FileNotFoundError(
                    f"找不到 workflow 目录: {artifact.workflow_id}, 无法保存 artifact"
                )
            artifact_dir = run_dir / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            path = artifact_dir / f"{artifact.id}.json"
        payload = json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        await atomic_write_text(path, payload + "\n")
        return path

    async def load(
        self,
        artifact_id: str,
        *,
        workflow_id: str | None = None,
        workspace_root: Path | None = None,
    ) -> Artifact | None:
        path = self._resolve_artifact_path(
            artifact_id,
            workflow_id=workflow_id,
            workspace_root=workspace_root,
        )
        if path is None or not path.exists():
            return None
        return await self._load_from_path(path)

    async def list_by_workflow(
        self,
        workflow_id: str,
        *,
        workspace_root: Path | None = None,
    ) -> list[Artifact]:
        artifact_dir = self._artifact_dir(
            workflow_id=workflow_id,
            workspace_root=workspace_root,
        )
        if artifact_dir is None or not artifact_dir.exists():
            return []
        out: list[Artifact] = []
        for path in sorted(artifact_dir.glob("*.json")):
            item = await self._load_from_path(path)
            if item is not None:
                out.append(item)
        return out

    async def list_by_phase(
        self,
        workflow_id: str,
        phase_id: str,
        *,
        workspace_root: Path | None = None,
    ) -> list[Artifact]:
        artifacts = await self.list_by_workflow(workflow_id, workspace_root=workspace_root)
        return [a for a in artifacts if a.phase_id == phase_id]

    async def search_by_type(
        self,
        *,
        workflow_id: str | None = None,
        artifact_type: str | ArtifactType | None = None,
        role: str | None = None,
        limit: int = 20,
        workspace_root: Path | None = None,
    ) -> list[Artifact]:
        wanted_type = _normalize_artifact_type(artifact_type)
        wanted_role = (role or "").strip().lower()
        if limit <= 0:
            return []

        items: list[Artifact] = []
        if workflow_id:
            items = await self.list_by_workflow(workflow_id, workspace_root=workspace_root)
        else:
            for path in self._iter_all_artifact_paths():
                item = await self._load_from_path(path)
                if item is not None:
                    items.append(item)

        filtered: list[Artifact] = []
        for item in items:
            if wanted_type is not None and item.type != wanted_type:
                continue
            if wanted_role and item.created_by_role.lower() != wanted_role:
                continue
            filtered.append(item)

        filtered.sort(key=lambda x: x.created_at, reverse=True)
        return filtered[:limit]

    async def migrate_legacy_phase_artifact(
        self,
        *,
        workflow_id: str,
        workspace_root: Path,
        phase_id: str,
        role: str,
        artifact_payload: dict[str, Any],
    ) -> Artifact:
        """把旧 state.json 里的 phase.artifact(dict) 迁移为独立 artifact 文件."""
        from forge.utils.id_generator import new_id

        summary = str(artifact_payload.get("summary") or "")
        title = str(artifact_payload.get("title") or f"{role}:{phase_id}")
        normalized_payload = dict(artifact_payload)
        normalized_payload.setdefault("legacy_migrated", True)
        artifact = Artifact(
            id=new_id("art"),
            workflow_id=workflow_id,
            phase_id=phase_id,
            type=_normalize_artifact_type(artifact_payload.get("type"))
            or ArtifactType.PHASE_OUTPUT,
            title=title,
            summary=summary,
            payload=normalized_payload,
            created_by_role=role,
        )
        await self.save(artifact, workspace_root=workspace_root)
        return artifact

    def _artifact_path(
        self,
        *,
        workflow_id: str,
        artifact_id: str,
        workspace_root: Path,
    ) -> Path:
        artifact_dir = paths.workflow_run_dir(workspace_root, workflow_id) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir / f"{artifact_id}.json"

    def _artifact_dir(
        self,
        *,
        workflow_id: str,
        workspace_root: Path | None = None,
    ) -> Path | None:
        if workspace_root is not None:
            return (
                paths.workflow_run_dir(workspace_root.expanduser().resolve(), workflow_id)
                / "artifacts"
            )
        run_dir = paths.find_workflow_run_dir(workflow_id)
        if run_dir is None:
            return None
        return run_dir / "artifacts"

    def _resolve_artifact_path(
        self,
        artifact_id: str,
        *,
        workflow_id: str | None = None,
        workspace_root: Path | None = None,
    ) -> Path | None:
        if workflow_id:
            artifact_dir = self._artifact_dir(
                workflow_id=workflow_id,
                workspace_root=workspace_root,
            )
            if artifact_dir is None:
                return None
            return artifact_dir / f"{artifact_id}.json"
        for path in self._iter_all_artifact_paths():
            if path.name == f"{artifact_id}.json":
                return path
        return None

    async def _load_from_path(self, path: Path) -> Artifact | None:
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                raw = await f.read()
            payload = json.loads(raw)
            return Artifact.from_dict(payload)
        except Exception:  # noqa: BLE001
            logger.exception("artifact 读取失败 path=%s", path)
            return None

    @staticmethod
    def _iter_all_artifact_paths() -> list[Path]:
        seen: set[str] = set()
        out: list[Path] = []
        for path in sorted(paths.projects_dir().glob("*/workflows/*/artifacts/*.json")):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(path)

        global_artifacts = paths.global_workspace_dir() / "workflows"
        if global_artifacts.exists():
            for path in sorted(global_artifacts.glob("*/artifacts/*.json")):
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                out.append(path)
        return out


def _normalize_artifact_type(value: str | ArtifactType | None) -> ArtifactType | None:
    if value is None:
        return None
    if isinstance(value, ArtifactType):
        return value
    raw = str(value).strip().lower()
    if not raw:
        return None
    try:
        return ArtifactType(raw)
    except ValueError:
        return None
