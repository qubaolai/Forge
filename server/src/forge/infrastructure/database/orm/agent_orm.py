"""Agent 表 - Agent (智能体) 配置。

一个 Agent 是一组完整的可执行配置: 提示词 + 模型 + 检索 + 工具。
复杂子结构 (collaborators / kb_ids / tools) 直接存 JSON, 不再拆子表。
"""

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import TimestampMixin, table_args
from forge.utils.id_generator import new_id


class AgentOrm(Base, TimestampMixin):
    """Agent 配置表。"""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default=lambda: new_id("agent"),
        comment="Agent ID, 形如 agent_xxx",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Agent 显示名称",
    )
    description: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Agent 简介, 列表卡片显示",
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="头像 URL",
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default="private",
        nullable=False,
        comment="可见性: private / workspace / public",
    )
    owner_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="拥有者用户 ID",
    )
    collaborators: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="协作者列表 (JSON): [{user_id, user_name, permission}]",
    )

    # 提示词
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
        comment="系统提示词",
    )
    opening_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="开场白 (用户进入会话首条 assistant 消息)",
    )

    # Agent 执行模式: 决定走哪条 agent 链路.
    # 目前只实现 "react" (function-calling), 其他值会在 chat 路由被拒绝.
    # 预留: workflow / plan_execute / rag / supervisor (按需添加实现后启用).
    mode: Mapped[str] = mapped_column(
        String(32),
        default="react",
        nullable=False,
        server_default="react",
        comment="Agent 执行模式: react | workflow | plan_execute | rag | supervisor",
    )

    # 模型配置
    model_id: Mapped[str] = mapped_column(
        String(64),
        default="",
        nullable=False,
        comment="使用的模型端点 ID 或别名 (对应 model_endpoints.id)",
    )
    temperature: Mapped[float] = mapped_column(
        default=0.7,
        nullable=False,
        comment="采样温度",
    )
    max_tokens: Mapped[int] = mapped_column(
        default=2048,
        nullable=False,
        comment="单次生成最大 token 数",
    )
    top_p: Mapped[float] = mapped_column(
        default=1.0,
        nullable=False,
        comment="nucleus 采样 top_p",
    )

    # 检索配置
    kb_ids: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="挂载的知识库 ID 列表 (JSON)",
    )
    retrieval_top_k: Mapped[int] = mapped_column(
        default=5,
        nullable=False,
        comment="检索返回 top_k",
    )
    retrieval_score_threshold: Mapped[float] = mapped_column(
        default=0.0,
        nullable=False,
        comment="检索相关性最低分数阈值",
    )
    retrieval_hybrid: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        comment="是否启用混合检索 (向量+BM25)",
    )
    retrieval_rerank: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="是否对检索结果重排",
    )

    # 工具绑定
    tools: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="工具绑定列表 (JSON): [{tool_id, enabled, config}]",
    )

    # 高级
    context_window: Mapped[int] = mapped_column(
        default=128_000,
        nullable=False,
        comment="上下文窗口 token 上限 (默认 128k, 覆盖主流大模型: GPT-4o / Sonnet 4 / Qwen-Plus / DeepSeek-V3 等)",
    )
    timeout_seconds: Mapped[int] = mapped_column(
        default=60,
        nullable=False,
        comment="单次 LLM 调用超时秒数",
    )
    max_retries: Mapped[int] = mapped_column(
        default=2,
        nullable=False,
        comment="LLM 调用失败重试次数",
    )
    enable_streaming: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        comment="是否开启流式输出",
    )

    __table_args__ = table_args(
        Index("ix_agents_owner", "owner_id"),
        Index("ix_agents_visibility", "visibility"),
        comment="Agent 智能体配置表",
    )
