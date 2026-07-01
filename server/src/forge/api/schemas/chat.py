"""会话与对话相关的 schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge.api.schemas.file import FileMeta


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
    # 上下文占用快照 (分层), 仅 assistant 消息有值; 由 context_meta 派生, 供前端持久化展示
    context_usage: dict | None = None
    # 该消息关联的会话文件 (user 消息=上传附件; assistant 消息=write_file 生成), 读时按 message_id join
    files: list[FileMeta] = Field(default_factory=list)
    created_at: datetime


# ---- 对话 ----
class ChatAttachment(BaseModel):
    """对话附件占位(后续接知识库时扩展)。"""

    type: str
    id: str | None = None
    file_id: str | None = None
    name: str | None = None


class ModelOptionsIn(BaseModel):
    """模型选择 — 前端必须指定 provider 和 model。

    统一思考参数:
    - thinking: 是否开启思考
    - thinking_level: 思考强度档位 (由前端映射)
    """

    provider: str
    model: str
    thinking: bool | None = None
    thinking_level: Literal["low", "medium", "high", "xhigh"] | None = None


class ChatCompletionIn(BaseModel):
    """聊天对话请求 — 纯 Web 对话模式。"""

    session_id: str | None = None
    # 上限兜底: 超大输入应由前端转「会话附件」(POST /chat/attachments) 走 read_file 按需读取,
    # 此处仅防绕过前端直接灌超大 body 撑爆上下文。
    message: str = Field(min_length=1, max_length=100_000)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    kb_ids: list[str] = Field(default_factory=list, description="本轮允许查询的知识库 ID 列表")
    model_options: ModelOptionsIn


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
