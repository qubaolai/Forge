"""TaskOptions — 用户传入参数、系统默认、硬上限三层合并链。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class HardCaps:
    max_agents: int = 8
    max_steps_per_task: int = 50
    max_replans: int = 3
    max_task_retries: int = 2
    max_run_duration_sec: float = 3600.0


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
        caps = HardCaps()
        if hasattr(settings, "task_execution") and hasattr(settings.task_execution, "hard_caps"):
            hc = settings.task_execution.hard_caps
            caps = HardCaps(**{k: getattr(hc, k) for k in vars(HardCaps()) if hasattr(hc, k)})

        def_opts = None
        if hasattr(settings, "task_execution"):
            def_opts = getattr(settings.task_execution, "default_options", None)

        merged_in = user_in or TaskOptionsIn()
        return cls(
            allow_write=merged_in.allow_write,
            allow_parallel=merged_in.allow_parallel,
            max_agents=min(merged_in.max_agents, caps.max_agents),
            writer_mode=merged_in.writer_mode,
            verifier_cmd=merged_in.verifier_cmd or (
                getattr(def_opts, "verifier_cmd", None) if def_opts else None
            ),
            workspace_path=merged_in.workspace_path or "",
            hard_caps=caps,
        )
