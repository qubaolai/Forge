"""AdaptiveRun 状态存储与事件回放。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
from forge.config import paths

from forge.adaptive import events
from forge.adaptive.models import AdaptiveRun, Artifact, ArtifactKind, RunEvent, RunStatus
from forge.infrastructure.jsonl import JsonlLog, atomic_write_text
from forge.utils.id_generator import new_id


class AdaptiveRunStore:
    """Adaptive 运行态文件存储。

    目录结构:
    - `<project_state>/adaptive_runs/<run_id>/state.json`
    - `<project_state>/adaptive_runs/<run_id>/events.jsonl`
    - `<project_state>/adaptive_runs/<run_id>/artifacts/*.json`
    """

    def __init__(
        self,
        workspace_path: str | Path | None = None,
        *,
        base_dir: str | Path | None = None,
    ) -> None:
        self._workspace_root = self._resolve_workspace_root(workspace_path)
        if base_dir is None:
            self._runs_dir = paths.project_state_dir(self._workspace_root) / "adaptive_runs"
        else:
            self._runs_dir = Path(base_dir).expanduser().resolve() / "adaptive_runs"
        # 根目录 mkdir 是一次性同步开销，可接受；后续 per-run 目录走 lazy 缓存
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        # 已创建过的 run 子目录缓存，避免每次 append_event / save_artifact 都 mkdir
        self._ensured_dirs: set[str] = set()

    async def save_run(self, run: AdaptiveRun) -> Path:
        """保存 run 状态快照。"""
        path = self._state_path(run.run_id)
        content = json.dumps(run.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        await atomic_write_text(path, content)
        return path

    async def load_run(self, run_id: str) -> AdaptiveRun | None:
        """读取 run 状态快照。"""
        path = self._state_path(run_id)
        if not path.exists():
            return None
        async with aiofiles.open(path, encoding="utf-8") as f:
            payload = json.loads(await f.read() or "{}")
        return AdaptiveRun.from_dict(payload)

    async def list_runs(
        self,
        *,
        limit: int = 100,
        owner_user_id: str | None = None,
    ) -> list[AdaptiveRun]:
        """列出当前 workspace 下的 run。

        ``owner_user_id`` 非空时只返回该 owner 的 run（多租户隔离）。
        """
        if limit <= 0:
            return []
        items: list[AdaptiveRun] = []
        for state_path in sorted(self._runs_dir.glob("*/state.json"), reverse=True):
            async with aiofiles.open(state_path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            run = AdaptiveRun.from_dict(payload)
            if owner_user_id is not None and run.owner_user_id != owner_user_id:
                continue
            items.append(run)
            if len(items) >= limit:
                break
        return items

    async def append_event(self, run_id: str, event_type: str, payload: dict | None = None) -> RunEvent:
        """追加事件。"""
        event = RunEvent(
            id=new_id("evt"),
            run_id=run_id,
            type=event_type,
            ts=datetime.now(UTC),
            payload=dict(payload or {}),
        )
        log = JsonlLog(self._events_path(run_id), fsync=True)
        await log.append(event.to_dict())
        return event

    async def list_events(self, run_id: str, *, after_event_id: str | None = None) -> list[RunEvent]:
        """读取事件流，可选从某个事件之后回放。"""
        log = JsonlLog(self._events_path(run_id), fsync=False)
        rows: list[dict] = []
        if after_event_id:
            rows = [row async for row in log.iter_after(lambda r: r.get("id") == after_event_id)]
        else:
            rows = [row async for row in log.iter_all()]
        return [RunEvent.from_dict(row) for row in rows]

    async def has_event(self, run_id: str, event_id: str) -> bool:
        """B4/P1-8: 检查事件 id 是否在 events.jsonl 中存在，用于 SSE cursor 校验。"""
        log = JsonlLog(self._events_path(run_id), fsync=False)
        async for row in log.iter_all():
            if row.get("id") == event_id:
                return True
        return False

    async def transition_status(
        self,
        run_id: str,
        to_status: RunStatus,
        *,
        payload: dict | None = None,
    ) -> AdaptiveRun:
        """更新 run 状态并写事件。"""
        run = await self.load_run(run_id)
        if run is None:
            raise FileNotFoundError(f"run 不存在: {run_id}")
        if not run.can_transition_to(to_status):
            raise ValueError(f"非法状态流转: {run.status.value} -> {to_status.value}")

        from_status = run.status
        run.status = to_status
        run.updated_at = datetime.now(UTC)
        await self.save_run(run)
        await self.append_event(
            run_id,
            events.RUN_STATUS_CHANGED,
            {
                "from_status": from_status.value,
                "to_status": to_status.value,
                **dict(payload or {}),
            },
        )
        return run

    async def save_artifact(self, artifact: Artifact) -> Path:
        """保存 artifact。"""
        path = self._artifact_path(artifact.run_id, artifact.artifact_id)
        content = json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        await atomic_write_text(path, content)
        return path

    async def load_artifact(self, run_id: str, artifact_id: str) -> Artifact | None:
        """读取 artifact。"""
        path = self._artifact_path(run_id, artifact_id)
        if not path.exists():
            return None
        async with aiofiles.open(path, encoding="utf-8") as f:
            payload = json.loads(await f.read() or "{}")
        return Artifact.from_dict(payload)

    async def list_artifacts(
        self,
        run_id: str,
        *,
        kind: ArtifactKind | None = None,
        task_id: str | None = None,
    ) -> list[Artifact]:
        """按条件列出 artifact。"""
        artifact_dir = self._artifact_dir(run_id)
        if not artifact_dir.exists():
            return []
        items: list[Artifact] = []
        for path in sorted(artifact_dir.glob("*.json")):
            async with aiofiles.open(path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            item = Artifact.from_dict(payload)
            if kind is not None and item.kind != kind:
                continue
            if task_id is not None and item.task_id != task_id:
                continue
            items.append(item)
        return items

    async def find_artifact(
        self,
        artifact_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> Artifact | None:
        """跨 run 查找 artifact。

        ``owner_user_id`` 非空时校验关联 run 的 owner，越权返回 None。
        """
        for path in sorted(self._runs_dir.glob(f"*/artifacts/{artifact_id}.json")):
            async with aiofiles.open(path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            artifact = Artifact.from_dict(payload)
            if owner_user_id is not None:
                owner = await self._artifact_owner(artifact)
                if owner != owner_user_id:
                    return None
            return artifact
        return None

    async def list_artifacts_across_runs(
        self,
        *,
        run_id: str | None = None,
        kind: ArtifactKind | None = None,
        task_id: str | None = None,
        limit: int = 200,
        owner_user_id: str | None = None,
    ) -> list[Artifact]:
        """按条件聚合 artifact（支持跨 run）。

        ``owner_user_id`` 非空时仅返回该 owner 的 run 关联 artifact。
        """
        if limit <= 0:
            return []
        if run_id:
            if owner_user_id is not None:
                run = await self.load_run(run_id)
                if run is None or run.owner_user_id != owner_user_id:
                    return []
            return (await self.list_artifacts(run_id, kind=kind, task_id=task_id))[:limit]

        # 跨 run 聚合：先建 run_id → owner 的映射，避免逐个 artifact 反查
        owner_map: dict[str, str] = {}
        if owner_user_id is not None:
            for state_path in self._runs_dir.glob("*/state.json"):
                async with aiofiles.open(state_path, encoding="utf-8") as f:
                    raw = json.loads(await f.read() or "{}")
                owner_map[str(raw.get("run_id", ""))] = str(raw.get("owner_user_id", ""))

        items: list[Artifact] = []
        for path in sorted(self._runs_dir.glob("*/artifacts/*.json"), reverse=True):
            async with aiofiles.open(path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            item = Artifact.from_dict(payload)
            if kind is not None and item.kind != kind:
                continue
            if task_id is not None and item.task_id != task_id:
                continue
            if owner_user_id is not None and owner_map.get(item.run_id) != owner_user_id:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    async def _artifact_owner(self, artifact: Artifact) -> str | None:
        """读取 artifact 关联 run 的 owner_user_id。"""
        run = await self.load_run(artifact.run_id)
        return run.owner_user_id if run is not None else None

    def _ensure_run_dir(self, run_id: str) -> Path:
        """惰性确保 run 子目录存在；首次创建后缓存，避免每次事件都 mkdir 阻塞事件循环。"""
        run_dir = self._runs_dir / run_id
        if run_id not in self._ensured_dirs:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            self._ensured_dirs.add(run_id)
        return run_dir

    def _state_path(self, run_id: str) -> Path:
        return self._ensure_run_dir(run_id) / "state.json"

    def _events_path(self, run_id: str) -> Path:
        return self._ensure_run_dir(run_id) / "events.jsonl"

    def _artifact_dir(self, run_id: str) -> Path:
        return self._ensure_run_dir(run_id) / "artifacts"

    def _artifact_path(self, run_id: str, artifact_id: str) -> Path:
        return self._artifact_dir(run_id) / f"{artifact_id}.json"

    @staticmethod
    def _resolve_workspace_root(workspace_path: str | Path | None) -> Path:
        if workspace_path is None:
            return paths.global_workspace_dir()
        text = str(workspace_path).strip()
        if not text:
            return paths.global_workspace_dir()
        return Path(text).expanduser().resolve()
