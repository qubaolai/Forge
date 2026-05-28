"""通用 Run 持久化层 (CLI 模式: plan_exec / workflow / ...).

目录结构 (per workspace):
    <project_state>/runs/<run_id>/state.json
    <project_state>/runs/<run_id>/events.jsonl
    <project_state>/runs/<run_id>/artifacts/<artifact_id>.json

设计要点 (vs adaptive/store.py):
    - status 是开放字符串, 不锁状态机. 业务侧需要校验时传 StatusValidator.
    - RunRecord 是通用最小集; mode 特有字段塞 metadata.
    - artifact.kind 是开放字符串, 不再锁 ArtifactKind 枚举.
    - 复用 infrastructure/jsonl.py 的 JsonlLog / atomic_write_text 原语.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

from forge.config import paths
from forge.infrastructure.jsonl import JsonlLog, atomic_write_text
from forge.infrastructure.run_store_models import (
    Artifact,
    RunEvent,
    RunRecord,
)
from forge.utils.id_generator import new_id

# StatusValidator: 由 mode lifecycle 自己实现; 默认放行所有转移.
# 签名: (record, to_status) -> raise ValueError 表示非法转移
StatusValidator = Callable[[RunRecord, str], Awaitable[None] | None]


class RunStore:
    """通用 Run 文件存储."""

    def __init__(
        self,
        workspace_path: str | Path | None = None,
        *,
        base_dir: str | Path | None = None,
        dir_name: str = "runs",
    ) -> None:
        self._workspace_root = self._resolve_workspace_root(workspace_path)
        if base_dir is None:
            self._runs_dir = paths.project_state_dir(self._workspace_root) / dir_name
        else:
            self._runs_dir = Path(base_dir).expanduser().resolve() / dir_name
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        # 已创建过的 run 子目录缓存, 避免每次 append_event / save_artifact 都 mkdir
        self._ensured_dirs: set[str] = set()

    # ------------------------------------------------------------------
    # Run 元数据
    # ------------------------------------------------------------------
    async def create_run(
        self,
        *,
        mode: str,
        goal: str = "",
        owner_user_id: str = "local",
        metadata: dict | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """新建 run, 持久化 state.json + 写 run_started 事件."""
        rid = run_id or new_id("run")
        record = RunRecord(
            run_id=rid,
            mode=mode,
            workspace_path=str(self._workspace_root),
            goal=goal,
            status="running",
            owner_user_id=owner_user_id,
            metadata=dict(metadata or {}),
        )
        await self.save_run(record)
        await self.append_event(rid, "run_started", {"mode": mode, "goal": goal})
        return record

    async def save_run(self, record: RunRecord) -> Path:
        """保存 run 状态快照 (原子写)."""
        path = self._state_path(record.run_id)
        content = (
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        await atomic_write_text(path, content)
        return path

    async def load_run(self, run_id: str) -> RunRecord | None:
        """读取 run 状态快照."""
        path = self._state_path(run_id)
        if not path.exists():
            return None
        async with aiofiles.open(path, encoding="utf-8") as f:
            payload = json.loads(await f.read() or "{}")
        return RunRecord.from_dict(payload)

    async def list_runs(
        self,
        *,
        mode: str | None = None,
        owner_user_id: str | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        """列出当前 workspace 下的 run, 可按 mode / owner 过滤."""
        if limit <= 0:
            return []
        items: list[RunRecord] = []
        for state_path in sorted(self._runs_dir.glob("*/state.json"), reverse=True):
            async with aiofiles.open(state_path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            record = RunRecord.from_dict(payload)
            if mode is not None and record.mode != mode:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            items.append(record)
            if len(items) >= limit:
                break
        return items

    async def transition_status(
        self,
        run_id: str,
        to_status: str,
        *,
        payload: dict | None = None,
        validator: StatusValidator | None = None,
    ) -> RunRecord:
        """更新 status 并写 run_status_changed 事件.

        validator 非空时, 在转移前回调让业务校验; 抛 ValueError 表示非法.
        """
        record = await self.load_run(run_id)
        if record is None:
            raise FileNotFoundError(f"run 不存在: {run_id}")
        if validator is not None:
            result = validator(record, to_status)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]

        from_status = record.status
        record.status = to_status
        record.updated_at = datetime.now(UTC)
        await self.save_run(record)
        await self.append_event(
            run_id,
            "run_status_changed",
            {
                "from_status": from_status,
                "to_status": to_status,
                **dict(payload or {}),
            },
        )
        return record

    # ------------------------------------------------------------------
    # 事件流
    # ------------------------------------------------------------------
    async def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> RunEvent:
        """追加事件到 events.jsonl (fsync 保证持久化)."""
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

    async def list_events(
        self,
        run_id: str,
        *,
        after_event_id: str | None = None,
    ) -> list[RunEvent]:
        """读取事件流, 可选从某个事件之后回放 (SSE cursor 用)."""
        log = JsonlLog(self._events_path(run_id), fsync=False)
        rows: list[dict] = []
        if after_event_id:
            rows = [
                row async for row in log.iter_after(
                    lambda r: r.get("id") == after_event_id
                )
            ]
        else:
            rows = [row async for row in log.iter_all()]
        return [RunEvent.from_dict(row) for row in rows]

    async def has_event(self, run_id: str, event_id: str) -> bool:
        """检查事件 id 是否存在, 用于 SSE cursor 校验 (防客户端缓存过期)."""
        log = JsonlLog(self._events_path(run_id), fsync=False)
        async for row in log.iter_all():
            if row.get("id") == event_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Artifact
    # ------------------------------------------------------------------
    async def save_artifact(self, artifact: Artifact) -> Path:
        path = self._artifact_path(artifact.run_id, artifact.artifact_id)
        content = (
            json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        )
        await atomic_write_text(path, content)
        return path

    async def load_artifact(self, run_id: str, artifact_id: str) -> Artifact | None:
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
        kind: str | None = None,
        task_id: str | None = None,
    ) -> list[Artifact]:
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
        """跨 run 查找 artifact. owner 非空时校验越权."""
        for path in sorted(self._runs_dir.glob(f"*/artifacts/{artifact_id}.json")):
            async with aiofiles.open(path, encoding="utf-8") as f:
                payload = json.loads(await f.read() or "{}")
            artifact = Artifact.from_dict(payload)
            if owner_user_id is not None:
                run = await self.load_run(artifact.run_id)
                if run is None or run.owner_user_id != owner_user_id:
                    return None
            return artifact
        return None

    # ------------------------------------------------------------------
    # 路径辅助
    # ------------------------------------------------------------------
    def _ensure_run_dir(self, run_id: str) -> Path:
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


__all__ = ["RunStore", "StatusValidator"]
