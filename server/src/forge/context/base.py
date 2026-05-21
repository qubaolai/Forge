"""上下文构建的对外契约: Protocol + 数据类型.

ContextBuilder 是无状态编排者: 把 history / summary / facts / 当前用户消息
合成最终 messages, 并把降级信息写入 BuildMeta.

依赖 (构造时注入):
    - MessageRepository  -> 历史
    - MemoryStore        -> 摘要 + 事实
    - TokenCounter       -> 见 forge.llm.token_counter
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from forge.core.types.message import Message


# ---------------------------------------------------------------------------
# 1. 给 ContextBuilder 的 "Agent 视图": 只取构建上下文需要的字段.
#    不直接传 AgentOrm, 避免 context/ 反向依赖 infrastructure/database/.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentContextConfig:
    system_prompt: str
    context_window: int  # 模型上下文 token 上限
    history_limit: int = 30  # 最多取多少条原文历史 (再受 token 预算约束)
    enable_summary: bool = True  # 关闭时整段摘要不参与
    enable_facts: bool = True  # 关闭时不召回长期事实
    facts_top_k: int = 5


@dataclass(frozen=True)
class WorkspaceContextLayer:
    """Workspace layer injected into the system context."""

    workspace_id: str
    root_path: str
    assistant_prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowContextLayer:
    """Workflow layer injected into the system context."""

    workflow_id: str
    template_id: str
    mode: str = "light"
    role_artifacts: dict[str, Any] = field(default_factory=dict)
    recent_events: tuple[dict[str, Any], ...] = ()


# ---------------------------------------------------------------------------
# 2. 预算分配: 写死合理默认 + 留扩展口子.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BudgetConfig:
    """各组件的 token 预算份额, 相加 <= 1.0. 剩余部分留给 LLM 输出."""

    system_share: float = 0.20  # base_prompt + facts + summary 总份额
    history_share: float = 0.50  # 历史原文份额
    # output_share 隐式 = 1 - system_share - history_share (= 0.30)

    def validate(self) -> None:
        assert 0 < self.system_share < 1
        assert 0 < self.history_share < 1
        assert self.system_share + self.history_share < 1, "需要给输出留预算"


DEFAULT_BUDGET = BudgetConfig()


# ---------------------------------------------------------------------------
# 3. 入参: 一次构建请求.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BuildRequest:
    user_id: str  # 用于召回该用户的长期事实
    session_id: str  # 用于查历史 + 取摘要
    current_user_message: str  # 本轮用户输入 (最后会拼到 messages 末尾)
    agent: AgentContextConfig
    budget: BudgetConfig = DEFAULT_BUDGET
    exclude_message_ids: tuple[str, ...] = ()
    # 调用方在 "本轮已持久化的用户消息" 这种情况下传入其 id,
    # 避免该消息既出现在 history 又作为 current_user_message 被重复.
    workspace_id: str | None = None
    workflow_id: str | None = None
    workspace_context: WorkspaceContextLayer | None = None
    project_decisions: tuple[str, ...] = ()
    workflow_context: WorkflowContextLayer | None = None
    role_history: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 4. 出参: messages + 透明的降级元信息.
# ---------------------------------------------------------------------------
@dataclass
class BuildMeta:
    """构建过程的可观测信息, 方便写入 SSE done 事件或日志."""

    estimated_input_tokens: int = 0  # 最终 messages 估算 token
    history_messages_used: int = 0  # 实际带入 history 条数
    history_messages_dropped: int = 0  # 因预算丢掉的 history 条数
    summary_included: bool = False
    facts_included: int = 0  # 实际带入 fact 条数
    degraded: list[str] = field(default_factory=list)
    # 形如:
    #   ["summary_fetch_failed"]            <- MemoryStore.get_summary 抛错被吞
    #   ["facts_recall_failed"]             <- MemoryStore.recall_facts 抛错被吞
    #   ["history_truncated_by_budget"]     <- 历史被 token 预算截断
    #   ["active_compaction_triggered"]     <- 主动压缩兜底过 (R2)

    # ----- R2 主动压缩元信息 (R1 阶段全部保持默认 0/False, 行为不变) -----
    compaction_performed: bool = False  # 本次是否触发主动压缩
    compaction_token_saved: int = 0  # 压缩省了多少 token (压缩前 - 压缩后)
    rebuild_count: int = 0  # context build 重建几次 (主动压缩后会 rebuild)


@dataclass
class AssembledContext:
    """已组装好可直接喂给 LLM 的上下文.

    messages: 完整 messages 列表 (system + history + 当前 user), 顺序正确, 可直接调 LLM.
    meta:     组装过程的可观测信息, 包含降级 / 截断 / 压缩等元信息.
    """

    messages: list[Message]
    meta: BuildMeta


# ---------------------------------------------------------------------------
# 5. Protocol: 唯一对外契约. 当前阶段只有一个实现 CompositeContextBuilder.
# ---------------------------------------------------------------------------
class ContextBuilder(Protocol):
    """无状态. 同一实例可并发处理多个请求."""

    async def build(self, request: BuildRequest) -> AssembledContext: ...
