"""AgentProfile 配置模型 (统一治理所有 agent_mode).

模式定义在 sys_config.yaml 的 agent_profiles 段, 启动期 (lifespan startup)
校验工具 / 角色 / 模板存在性 - 任一失败直接拒绝启动.

字段语义:
    description:           人类可读说明 (列模式时展示用)
    system_prompt_template: prompts/{name}.j2 路径 (不含 .j2)
    tools_allowed:          该模式可见的工具白名单
    max_steps:              ReAct 兜底上限
    guards:                 启用的 LoopGuard 名 (默认全开)
    persistence:            chat_db (chat 走 DB) / none
    model_profile:          fast / smart / strong, 解析到 model_profiles 字典
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
    max_steps: int = Field(default=50, ge=1)
    guards: list[str] = Field(default_factory=list)
    persistence: Literal["chat_db", "none"] = "chat_db"
    model_profile: Literal["fast", "smart", "strong"] = "smart"


class AgentProfilesConfig(BaseModel):
    """sys_config.yaml 的 agent_profiles 段."""

    model_config = {"extra": "forbid"}

    model_profiles: ModelProfiles = Field(default_factory=ModelProfiles)
    profiles: dict[str, AgentProfile] = Field(default_factory=dict)


__all__ = ["AgentProfile", "AgentProfilesConfig", "ModelProfiles"]
