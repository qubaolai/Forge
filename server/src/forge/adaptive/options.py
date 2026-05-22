"""TaskOptions — 用户传入参数、系统默认、硬上限三层合并链。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class HardCaps:
    max_agents: int = 8
    max_steps_per_task: int = 50
    max_replans: int = 3
    max_task_retries: int = 2
    max_run_duration_sec: float = 3600.0

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> HardCaps:
        if not payload:
            return cls()
        defaults = cls()
        return cls(
            max_agents=int(payload.get("max_agents", defaults.max_agents)),
            max_steps_per_task=int(payload.get("max_steps_per_task", defaults.max_steps_per_task)),
            max_replans=int(payload.get("max_replans", defaults.max_replans)),
            max_task_retries=int(payload.get("max_task_retries", defaults.max_task_retries)),
            max_run_duration_sec=float(
                payload.get("max_run_duration_sec", defaults.max_run_duration_sec)
            ),
        )


class TaskOptionsIn(BaseModel):
    """用户可传的覆盖参数（任意字段均可省略）。"""

    allow_write: bool = True
    allow_parallel: bool = True
    max_agents: int = Field(default=4, ge=1, le=8)
    writer_mode: Literal["direct", "isolated_worktree"] = "isolated_worktree"
    verifier_cmd: str | None = None
    workspace_path: str | None = None


@dataclass(frozen=True)
class TaskOptions:
    """合并后的最终选项。合并顺序：sys_config < TaskOptionsIn < HardCaps。"""

    allow_write: bool
    allow_parallel: bool
    max_agents: int
    writer_mode: str
    verifier_cmd: str | None
    workspace_path: str
    hard_caps: HardCaps

    @classmethod
    def build(cls, user_in: TaskOptionsIn | None, *, settings) -> TaskOptions:
        task_cfg = getattr(settings, "task_execution", None)
        hard_cfg = getattr(task_cfg, "hard_caps", None)
        default_cfg = getattr(task_cfg, "default_options", None)

        caps = HardCaps(
            max_agents=getattr(hard_cfg, "max_agents", HardCaps.max_agents),
            max_steps_per_task=getattr(hard_cfg, "max_steps_per_task", HardCaps.max_steps_per_task),
            max_replans=getattr(hard_cfg, "max_replans", HardCaps.max_replans),
            max_task_retries=getattr(hard_cfg, "max_task_retries", HardCaps.max_task_retries),
            max_run_duration_sec=getattr(
                hard_cfg,
                "max_run_duration_sec",
                HardCaps.max_run_duration_sec,
            ),
        )

        base_values = {
            "allow_write": getattr(default_cfg, "allow_write", True),
            "allow_parallel": getattr(default_cfg, "allow_parallel", True),
            "max_agents": getattr(default_cfg, "max_agents", 4),
            "writer_mode": getattr(default_cfg, "writer_mode", "isolated_worktree"),
            "verifier_cmd": getattr(default_cfg, "verifier_cmd", None),
            "workspace_path": "",
        }

        user_values: dict[str, object] = {}
        if user_in is not None:
            user_values = user_in.model_dump(exclude_unset=True)
        merged = {**base_values, **user_values}

        max_agents = int(merged["max_agents"])
        max_agents = max(1, min(max_agents, caps.max_agents))
        # workspace_path 不在 build 阶段填默认值，避免误用 server cwd；
        # 入口路由层负责对"必须有 workspace 的操作"做 400 校验。
        workspace_raw = str(merged["workspace_path"] or "").strip()
        workspace_path = (
            str(Path(workspace_raw).expanduser().resolve()) if workspace_raw else ""
        )

        return cls(
            allow_write=bool(merged["allow_write"]),
            allow_parallel=bool(merged["allow_parallel"]),
            max_agents=max_agents,
            writer_mode=str(merged["writer_mode"]),
            verifier_cmd=str(merged["verifier_cmd"]) if merged["verifier_cmd"] else None,
            workspace_path=workspace_path,
            hard_caps=caps,
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化用于持久化到 AdaptiveRun.options_snapshot。"""
        return {
            "allow_write": self.allow_write,
            "allow_parallel": self.allow_parallel,
            "max_agents": self.max_agents,
            "writer_mode": self.writer_mode,
            "verifier_cmd": self.verifier_cmd,
            "workspace_path": self.workspace_path,
            "hard_caps": self.hard_caps.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> TaskOptions:
        """从持久化快照恢复 TaskOptions（用于 run 重启 / decide continue）。"""
        if not payload:
            return cls(
                allow_write=True,
                allow_parallel=True,
                max_agents=4,
                writer_mode="isolated_worktree",
                verifier_cmd=None,
                workspace_path="",
                hard_caps=HardCaps(),
            )
        return cls(
            allow_write=bool(payload.get("allow_write", True)),
            allow_parallel=bool(payload.get("allow_parallel", True)),
            max_agents=int(payload.get("max_agents", 4)),
            writer_mode=str(payload.get("writer_mode", "isolated_worktree")),
            verifier_cmd=payload.get("verifier_cmd"),
            workspace_path=str(payload.get("workspace_path") or ""),
            hard_caps=HardCaps.from_dict(payload.get("hard_caps")),
        )
