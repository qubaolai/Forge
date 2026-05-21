"""Agent 相关 Pydantic 模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Agent 执行模式. 当前仅 "react" 真正实现, 其余预留 (chat 路由会拒绝).
AgentMode = Literal["react", "workflow", "plan_execute", "rag", "supervisor"]


class CollaboratorSchema(BaseModel):
    user_id: str
    user_name: str = ""
    permission: str = "read"


class ModelConfigSchema(BaseModel):
    model_id: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 1.0


class RetrievalConfigSchema(BaseModel):
    kb_ids: list[str] = Field(default_factory=list)
    top_k: int = 5
    score_threshold: float = 0.0
    hybrid: bool = True
    rerank: bool = False


class ToolBindingSchema(BaseModel):
    tool_id: str
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class AgentAdvancedConfigSchema(BaseModel):
    context_window: int = (
        128_000  # 默认 128k 覆盖主流模型; 小窗模型 (qwen-flash 等) 在 Agent 创建时显式设
    )
    timeout_seconds: int = 60
    max_retries: int = 2
    enable_streaming: bool = True


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    avatar_url: str | None = None
    visibility: str
    owner_id: str
    collaborators: list[CollaboratorSchema] = Field(default_factory=list)
    mode: AgentMode = "react"
    system_prompt: str = ""
    opening_message: str | None = None
    model: ModelConfigSchema = Field(default_factory=ModelConfigSchema)
    retrieval: RetrievalConfigSchema = Field(default_factory=RetrievalConfigSchema)
    tools: list[ToolBindingSchema] = Field(default_factory=list)
    advanced: AgentAdvancedConfigSchema = Field(default_factory=AgentAdvancedConfigSchema)
    created_at: datetime
    updated_at: datetime


class AgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    avatar_url: str | None = None
    visibility: str = "private"
    collaborators: list[CollaboratorSchema] = Field(default_factory=list)
    mode: AgentMode = "react"
    system_prompt: str = ""
    opening_message: str | None = None
    model: ModelConfigSchema = Field(default_factory=ModelConfigSchema)
    retrieval: RetrievalConfigSchema = Field(default_factory=RetrievalConfigSchema)
    tools: list[ToolBindingSchema] = Field(default_factory=list)
    advanced: AgentAdvancedConfigSchema = Field(default_factory=AgentAdvancedConfigSchema)


class AgentUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    avatar_url: str | None = None
    visibility: str | None = None
    collaborators: list[CollaboratorSchema] | None = None
    mode: AgentMode | None = None
    system_prompt: str | None = None
    opening_message: str | None = None
    model: ModelConfigSchema | None = None
    retrieval: RetrievalConfigSchema | None = None
    tools: list[ToolBindingSchema] | None = None
    advanced: AgentAdvancedConfigSchema | None = None


def orm_to_agent_out(orm: Any) -> AgentOut:
    """ORM -> AgentOut, 将扁平字段聚合回前端期望的嵌套结构。"""
    return AgentOut(
        id=orm.id,
        name=orm.name,
        description=orm.description,
        avatar_url=orm.avatar_url,
        visibility=orm.visibility,
        owner_id=orm.owner_id,
        collaborators=[
            CollaboratorSchema(**c) if isinstance(c, dict) else c for c in (orm.collaborators or [])
        ],
        mode=getattr(orm, "mode", "react") or "react",
        system_prompt=orm.system_prompt or "",
        opening_message=orm.opening_message,
        model=ModelConfigSchema(
            model_id=orm.model_id or "",
            temperature=orm.temperature,
            max_tokens=orm.max_tokens,
            top_p=orm.top_p,
        ),
        retrieval=RetrievalConfigSchema(
            kb_ids=list(orm.kb_ids or []),
            top_k=orm.retrieval_top_k,
            score_threshold=orm.retrieval_score_threshold,
            hybrid=orm.retrieval_hybrid,
            rerank=orm.retrieval_rerank,
        ),
        tools=[ToolBindingSchema(**t) if isinstance(t, dict) else t for t in (orm.tools or [])],
        advanced=AgentAdvancedConfigSchema(
            context_window=orm.context_window,
            timeout_seconds=orm.timeout_seconds,
            max_retries=orm.max_retries,
            enable_streaming=orm.enable_streaming,
        ),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )
