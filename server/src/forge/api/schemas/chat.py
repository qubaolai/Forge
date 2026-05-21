"""会话与对话相关的 schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge.adaptive.options import TaskOptionsIn


# ---- 会话 ----
class SessionOut(BaseModel):
    """会话信息 + 关联 Agent 名字 + 消息数 (前端 ChatSession 类型期望)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    agent_id: str
    agent_name: str = ""
    title: str
    message_count: int = 0
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SessionCreateIn(BaseModel):
    agent_id: str = "default"
    title: str | None = None


class SessionUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)


# ---- 消息 ----
class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    status: str
    citations: Any | None = None
    tool_calls: Any | None = None
    usage: Any | None = None
    parent_id: str | None = None
    error_message: str | None = None
    reasoning_content: str | None = None  # 思考链 (DeepSeek thinking 等), 仅 assistant
    reasoning_duration_ms: int | None = None  # 思考累计墙钟毫秒, 仅 assistant
    created_at: datetime


# ---- 对话 ----
class ChatAttachment(BaseModel):
    """对话附件占位(后续接知识库时扩展)。"""

    type: str
    id: str | None = None
    file_id: str | None = None
    name: str | None = None


class ChatWorkflowOption(BaseModel):
    """对话发起时的 workflow 选项 (S6.5 M1: 统一入口).

    语义:
    - 字段不出现 → 走原 chat 路径 (单 phase ReAct loop).
    - `template_id` 显式 → 直接跑该 workflow 模板, 跳过 triage.
    - `mode="auto"` → 服务端 triage 决定. 命中 workflow 模板走 workflow;
      未命中 (落到对话级模板如 question_only) 时降级回 chat 路径.
    - `template_id` 与 `mode` 互斥, 只能选一个.
    """

    template_id: str | None = None
    mode: Literal["auto"] | None = None
    pause_after_phase: bool = False

    @field_validator("template_id")
    @classmethod
    def _validate_template_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # 局部 import 避免 schema 加载期触发 TemplateLoader 副作用.
        from forge.orchestration.workflow.template_loader import TemplateLoader

        known = set(TemplateLoader().load_all(refresh=True).keys())
        if v not in known:
            raise ValueError(f"未知 workflow 模板 '{v}', 可选: {sorted(known)}")
        return v

    @model_validator(mode="after")
    def _mutually_exclusive(self) -> "ChatWorkflowOption":
        if self.template_id and self.mode:
            raise ValueError("workflow.template_id 与 workflow.mode 不能同时设置")
        return self


class ChatCompletionIn(BaseModel):
    session_id: str | None = None
    agent_id: str | None = None  # 无 session_id 时用于建会话
    message: str = Field(min_length=1)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    override_retrieval: dict | None = None
    model_options: dict | None = None

    # Forge: 路由模式
    mode: Literal["auto", "chat", "task"] = "auto"
    # Forge: adaptive run 选项（mode=task 或 auto 路由为 task 时生效）
    task_options: TaskOptionsIn | None = None

    # deprecated: 由 mode/task_options 替代，保留一个版本做兼容
    workflow: ChatWorkflowOption | None = None


class ChatStopIn(BaseModel):
    message_id: str


class ChatRegenerateIn(BaseModel):
    message_id: str


class ChatResumeIn(BaseModel):
    """继续未完成的 assistant 消息.

    message_id: 上次 task_partial 事件返回的 message_id. 状态必须是
                aborted / partial; 其他状态 (done/error/streaming) 拒绝.
    """

    message_id: str
