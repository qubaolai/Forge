"""会话与对话相关的 schema。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    # N21: 为"会话内多 run 切换 / run 关联到 session"预留字段。
    # 当前阶段后端不维护，由前端/CLI 在订阅 SSE 时累积；schema 占位避免
    # 后续接入 RunsPage 时还需要破坏性变更。
    run_ids: list[str] = Field(default_factory=list)


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


class ChatCompletionIn(BaseModel):
    """聊天对话请求 — 纯对话模式，不包含任务执行。

    任务执行请走 POST /api/v1/runs。
    """

    session_id: str | None = None
    agent_id: str | None = None  # 无 session_id 时用于建会话
    message: str = Field(min_length=1)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    override_retrieval: dict | None = None
    model_options: dict | None = None


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
