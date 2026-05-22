"""Adaptive Task Execution 配置模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskExecutionModelProfiles(BaseModel):
    """任务执行使用的模型档位映射。"""

    model_config = {"extra": "forbid"}

    fast: str = "claude-sonnet-4-6"
    smart: str = "claude-sonnet-4-6"
    strong: str = "claude-opus-4-7"


class TaskExecutionHardCaps(BaseModel):
    """任务执行硬上限。"""

    model_config = {"extra": "forbid"}

    max_agents: int = Field(default=8, ge=1)
    max_steps_per_task: int = Field(default=50, ge=1)
    max_replans: int = Field(default=3, ge=0)
    max_task_retries: int = Field(default=2, ge=0)
    max_run_duration_sec: float = Field(default=3600.0, gt=0)


class TaskExecutionDefaultOptions(BaseModel):
    """任务执行默认选项。"""

    model_config = {"extra": "forbid"}

    allow_write: bool = True
    allow_parallel: bool = True
    max_agents: int = Field(default=4, ge=1)
    writer_mode: Literal["direct", "isolated_worktree"] = "isolated_worktree"
    verifier_cmd: str | None = None


class TaskExecutionConfig(BaseModel):
    """Adaptive 任务执行总配置。"""

    model_config = {"extra": "forbid"}

    chat_tool_allowlist: list[str] = Field(default_factory=lambda: ["knowledge_search"])
    tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "glob_search",
            "grep",
            "shell",
            "git_ops",
            "http_request",
            "run_tests",
            "knowledge_search",
        ]
    )
    model_profiles: TaskExecutionModelProfiles = Field(default_factory=TaskExecutionModelProfiles)
    mode_router_model_profile: Literal["fast", "smart", "strong"] = "fast"
    planner_model_profile: Literal["fast", "smart", "strong"] = "smart"
    discovery_model_profile: Literal["fast", "smart", "strong"] = "fast"
    hard_caps: TaskExecutionHardCaps = Field(default_factory=TaskExecutionHardCaps)
    default_options: TaskExecutionDefaultOptions = Field(default_factory=TaskExecutionDefaultOptions)
    # B7/B8/B18/C10: 是否在 supervisor 自动接入真实 Discovery Agent + Planner LLM + TaskRunner
    # C10/D1 决策：默认 true。LLM 不可用时 run 直接 FAILED，不退化为 fallback 假完成。
    # 测试/demo 可显式置 false 走兜底，但 sys_config.test.yaml 也已显式覆盖。
    enable_real_llm: bool = True
