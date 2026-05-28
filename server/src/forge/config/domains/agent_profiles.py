"""AgentProfile 配置模型 (统一治理所有 agent_mode).

模式定义在 sys_config.yaml 的 agent_profiles 段, 启动期 (lifespan startup)
校验工具 / 角色 / 模板存在性 - 任一失败直接拒绝启动.

字段语义:
    description:           人类可读说明 (列模式时展示用)
    system_prompt_template: prompts/{name}.j2 路径 (不含 .j2)
    tools_allowed:          全集 (用户批准后 Exec 阶段可见)
    readonly_tools:         plan_mode_initial=True 时 Plan 阶段可见的子集
    sub_agents_allowed:     spawn_subagent 时允许派发的 role 白名单
    plan_mode_initial:      初始进入 Plan Mode (lifecycle 切 readonly_tools)
    max_steps:              ReAct 兜底上限
    guards:                 启用的 LoopGuard 名 (默认全开)
    persistence:            chat_db (chat 走 DB) / run_store (CLI 走 JSONL) / none
    requires_template:      workflow 模式必填 body.workflow_template
    model_profile:          fast / smart / strong, 解析到 model_profiles 字典
    large_artifact_threshold_bytes: 工具结果超过此值落 artifact 回灌占位
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelProfiles(BaseModel):
    """模型档位映射, 必须 provider:model 格式."""

    model_config = {"extra": "forbid"}

    fast: str = "dashscope:qwen-plus"
    smart: str = "dashscope:qwen-plus"
    strong: str = "dashscope:qwen3-max-preview"


class AgentProfile(BaseModel):
    """单个 agent_mode 的完整配置."""

    model_config = {"extra": "forbid"}

    description: str = ""
    system_prompt_template: str
    tools_allowed: list[str] = Field(default_factory=list)
    readonly_tools: list[str] = Field(default_factory=list)
    sub_agents_allowed: list[str] = Field(default_factory=list)
    plan_mode_initial: bool = False
    max_steps: int = Field(default=50, ge=1)
    guards: list[str] = Field(default_factory=list)
    persistence: Literal["chat_db", "run_store", "none"] = "chat_db"
    requires_template: bool = False
    model_profile: Literal["fast", "smart", "strong"] = "smart"
    large_artifact_threshold_bytes: int = Field(default=8192, ge=0)


class AgentProfilesConfig(BaseModel):
    """sys_config.yaml 的 agent_profiles 段."""

    model_config = {"extra": "forbid"}

    model_profiles: ModelProfiles = Field(default_factory=ModelProfiles)
    profiles: dict[str, AgentProfile] = Field(default_factory=dict)


__all__ = ["AgentProfile", "AgentProfilesConfig", "ModelProfiles"]
