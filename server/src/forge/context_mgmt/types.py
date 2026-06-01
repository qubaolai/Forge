"""上下文管理系统的统一值对象 (跨子系统的共享数据类型).

设计:
    - ContextRequest:   统一入参 (chat / task / workflow 三模式均使用).
    - ContextSnapshot:  统一出参 (含 messages + 用量 + 降级信息).
    - ContextUsage:     近实时用量视图 (CLI / 前端展示).
    - WindowBudget:     单次请求按 mode 分配的 token 预算 (不含 output).
    - ContentChunk:     ContentProvider 的输出单元 (带 layer + token 预估).
    - HistoryMessage:   带元信息的历史消息 (供 HistoryFilter 决策).
    - CompactionResult: 压缩操作的可观测输出.

风格约定 (与 forge/context/base.py 一致):
    - 入参用 frozen dataclass (不可变).
    - 出参 / 中间状态用普通 dataclass.
    - 注释中文.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from forge.core.types.message import Message


# ---------------------------------------------------------------------------
# 0. 层叠上下文层 (从旧 forge/context/base.py 迁入).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WorkspaceContextLayer:
    """注入 system 上下文的 workspace 层."""

    workspace_id: str
    root_path: str
    assistant_prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowContextLayer:
    """注入 system 上下文的 workflow 层."""

    workflow_id: str
    template_id: str
    mode: str = "light"
    role_artifacts: dict[str, Any] = field(default_factory=dict)
    recent_events: tuple[dict[str, Any], ...] = ()


# ---------------------------------------------------------------------------
# 1. ContextMode: 三种业务模式, 驱动默认 Filter / Policy / Strategy 选择.
# ---------------------------------------------------------------------------
class ContextMode(str, Enum):
    """上下文管理的业务模式."""

    CHAT = "chat"          # 对话模式: hybrid filter + 截断 tool 结果 + summary 压缩
    TASK = "task"          # 代码任务模式: 无对话历史 + evict tool 结果 + 无压缩
    WORKFLOW = "workflow"  # 工作流模式: step 隔离 + 摘要 tool 结果 + 无压缩


# ---------------------------------------------------------------------------
# 2. WindowBudget: 单次请求的 token 输入分配 (不包含 output).
#    output 是计算属性 = context_window - total_input_budget,
#    调用方拿去当 LLM max_tokens 参数.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WindowBudget:
    """单次构建的 token 预算分配.

    三个输入分区:
        - system_budget:      system prompt + facts + summary + workspace 等系统层
        - dialogue_budget:    纯对话轮次 (user / assistant 文本)
        - tool_result_budget: 工具结果 (经 ToolResultPolicy 处理后)

    output_budget 不在此处, 由 max_output_tokens 推出.
    """

    context_window: int
    system_budget: int
    dialogue_budget: int
    tool_result_budget: int

    @property
    def total_input_budget(self) -> int:
        return self.system_budget + self.dialogue_budget + self.tool_result_budget

    @property
    def max_output_tokens(self) -> int:
        """建议传给 LLM 的 max_tokens 参数."""
        return max(0, self.context_window - self.total_input_budget)

    def validate(self) -> None:
        assert self.context_window > 0, "context_window 必须为正"
        assert self.total_input_budget < self.context_window, "必须给输出留预算"


# ---------------------------------------------------------------------------
# 3. ContentChunk: ContentProvider 的输出单元.
#    每个 chunk 携带 estimated_tokens, 让 MessageAssembler 裁剪时不重复计数.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContentChunk:
    """单条内容素材.

    kind:        粗粒度分类 ("history" | "summary" | "facts" | "workspace" | "system").
    layer:       细粒度层名 (= LayerUsage.name, 用于用量聚合).
    messages:    转换好的 Message 列表 (可为空).
    text:        若是 system 层文本片段 (非 message), 这里放文本.
    estimated_tokens: Provider 预估的 token 数.
    message_count:    消息条数 (对 dialogue / tool_results 有意义).
    truncated:        本 chunk 是否被处理 / 截断过.
    """

    kind: str
    layer: str
    messages: list[Message] = field(default_factory=list)
    text: str = ""
    estimated_tokens: int = 0
    message_count: int = 0
    truncated: bool = False


# ---------------------------------------------------------------------------
# 4. HistoryMessage: 带元信息的历史消息, 供 HistoryFilter 做相关性决策.
# ---------------------------------------------------------------------------
@dataclass
class HistoryMessage:
    """带元信息的历史消息.

    turn_index:   第几轮对话 (同轮的 user/assistant/tool 消息共享一个 turn_index).
    is_tool_call: 该 assistant 消息含 tool_calls (轻量, 随轮次保留).
    is_tool_result: 该 user/tool 消息是 tool 调用的结果 (受 ToolResultPolicy 处理).
    tool_name:        工具名 (仅 is_tool_call / is_tool_result 时有意义).
    tool_result_tokens: 原始 token 数 (处理前), 用于统计 tokens_saved.
    relevance_score:  HistoryFilter 阶段写入的相关性得分 (1.0 = 默认保留).
    """

    message: Message
    id: str
    turn_index: int
    is_tool_call: bool = False
    is_tool_result: bool = False
    tool_name: str | None = None
    tool_result_tokens: int = 0
    relevance_score: float = 1.0


# ---------------------------------------------------------------------------
# 5. ContextRequest: 统一入参.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContextRequest:
    """一次上下文构建请求的完整描述.

    chat 路径:     由 TurnContext 映射而来.
    adaptive 路径: 由 TaskNode + workspace 信息映射而来.
    workflow 路径: 由 step + workflow_run 映射而来.
    """

    # ---- 身份 ----
    user_id: str
    session_id: str
    current_user_message: str

    # ---- 模式 (驱动默认配置) ----
    mode: ContextMode = ContextMode.CHAT

    # ---- system prompt ----
    system_prompt_template: str = "chat/default_system"  # Jinja2 模板名
    system_prompt_vars: dict[str, Any] = field(default_factory=dict)
    system_prompt_override: str = ""  # 非空时跳过模板渲染, 直接使用

    # ---- 模型参数 ----
    context_window: int = 128_000
    history_limit: int = 30  # filter 之前的候选历史条数上限

    # ---- memory 开关 ----
    enable_summary: bool = True
    enable_facts: bool = True
    facts_top_k: int = 5

    # ---- 历史过滤 ----
    exclude_message_ids: tuple[str, ...] = ()

    # ---- 层叠上下文 ----
    workspace_id: str | None = None
    workflow_id: str | None = None
    workspace_context: WorkspaceContextLayer | None = None
    workflow_context: WorkflowContextLayer | None = None
    project_decisions: tuple[str, ...] = ()
    role_history: tuple[str, ...] = ()

    # ---- 工作流模式专用 ----
    step_id: str | None = None
    step_inputs: dict[str, Any] = field(default_factory=dict)

    # ---- 调用方标识 (用于日志 / trace) ----
    caller: str = "chat"


# ---------------------------------------------------------------------------
# 6. ContextUsage: 近实时上下文用量分层视图.
# ---------------------------------------------------------------------------
# 健康信号阈值
_WARNING_RATIO = 0.70
_CRITICAL_RATIO = 0.90
_COMPACTION_RATIO = 0.85


@dataclass(frozen=True)
class LayerUsage:
    """单个上下文层的用量."""

    name: str          # 层名 (见 _STANDARD_LAYERS)
    token_count: int
    ratio: float       # token_count / context_window
    message_count: int = 0  # 消息条数 (对 dialogue / tool_results 有意义)
    truncated: bool = False  # 本层是否被裁剪过


# 标准层名 (按 system message 组合顺序排列)
_STANDARD_LAYERS = (
    "system_prompt",   # 基础系统提示词
    "workspace",       # ASSISTANT.md + workspace settings
    "facts",           # 长期用户事实
    "summary",         # 早期对话摘要
    "dialogue",        # 对话历史 (user / assistant 文本)
    "tool_results",    # 工具调用结果历史
    "workflow_step",   # 工作流步骤输入 + 跨步 artifacts
    "current_input",   # 本轮用户消息
)


@dataclass
class ContextUsage:
    """可直接渲染到 CLI / 前端的上下文用量快照."""

    context_window: int
    total_input_tokens: int
    max_output_tokens: int           # = context_window - total_input_tokens
    total_ratio: float               # total_input_tokens / context_window
    layers: list[LayerUsage] = field(default_factory=list)  # 有序

    @property
    def is_warning(self) -> bool:
        return self.total_ratio > _WARNING_RATIO

    @property
    def is_critical(self) -> bool:
        return self.total_ratio > _CRITICAL_RATIO

    @property
    def needs_compaction(self) -> bool:
        """是否到达压缩阈值 (调用方可据此提示用户)."""
        return self.total_ratio > _COMPACTION_RATIO


# ---------------------------------------------------------------------------
# 7. ContextSnapshot: 统一出参.
# ---------------------------------------------------------------------------
@dataclass
class ContextSnapshot:
    """已组装好的上下文, 含完整可观测信息.

    替代旧的 AssembledContext + BuildMeta 组合.
    """

    messages: list[Message]
    budget: WindowBudget
    usage: ContextUsage

    # 渲染后的 system prompt 文本 (compat 层用于反向映射回老接口)
    rendered_system_prompt: str = ""

    # 历史
    history_messages_candidate: int = 0   # filter 之前的候选数
    history_messages_used: int = 0
    history_messages_filtered: int = 0    # 被相关性过滤剔除
    history_messages_dropped: int = 0     # 被 token 预算裁掉

    # 工具结果处理
    tool_results_processed: int = 0
    tool_results_tokens_saved: int = 0

    # memory
    summary_included: bool = False
    facts_included: int = 0

    # 压缩
    compaction_performed: bool = False
    compaction_token_saved: int = 0
    rebuild_count: int = 0

    # 降级原因列表 (与现有 BuildMeta.degraded 语义一致)
    # 形如:
    #   "summary_fetch_failed", "facts_recall_failed",
    #   "history_truncated_by_budget", "active_compaction_triggered",
    #   "compaction_failed", "semantic_filter_fallback".
    degraded: list[str] = field(default_factory=list)

    @property
    def usage_ratio(self) -> float:
        """已用 token / 窗口大小."""
        if self.budget.context_window <= 0:
            return 0.0
        return self.usage.total_input_tokens / self.budget.context_window

    @property
    def is_healthy(self) -> bool:
        """没有任何降级."""
        return not self.degraded


# ---------------------------------------------------------------------------
# 8. CompactionResult: 压缩操作的可观测输出.
#    trigger_source 仅供 log / SSE, 不进 SummaryStore.
# ---------------------------------------------------------------------------
@dataclass
class CompactionResult:
    """CompactionStrategy.compact() 的返回值."""

    success: bool
    tokens_saved: int = 0
    strategy_used: str = ""       # "summary" | "selective_drop" | "hybrid" | "null"
    trigger_source: str = ""      # "threshold" | "explicit" | "scheduled"
    failure_reason: str = ""      # success=False 时填写
