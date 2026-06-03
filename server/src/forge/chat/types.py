"""chat turn 内部数据类型.

TurnContext: TurnPreparer 准备完返回, 后续所有步骤都消费它. 不可变.
RunResult:   AgentRunner 跑完累计的结果, TurnFinalizer 用它写 DB / 发 done 事件.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnContext:
    """一次 chat turn 的静态上下文 (preparation 阶段后冻结)."""

    user_id: str
    user_name: str
    session_id: str
    assistant_msg_id: str
    user_msg_id: str
    current_user_message: str
    agent_mode: str
    is_new_session: bool
    new_title: str | None  # 本轮是否自动重命名了 (None = 没改)
    trace_id: str
    model_options: dict[str, Any] | None = None
    # ★ ContextBuilder 需要排除本轮已持久化的 user 消息, 避免历史里重复
    exclude_message_ids: tuple[str, ...] = ()
    # 给上下文构建用的 agent 配置
    system_prompt: str = ""
    context_window: int = 128_000


@dataclass(frozen=True)
class ResumeState:
    """Resume 时, 从 DB 加载的 "上次未完成回复" 快照.

    Finalizer 用它把新一轮的 RunResult 和上次的内容 merge 起来 (追加, 不替换).
    """

    original_user_message: str  # 触发本轮 turn 的原始 user 消息
    prev_content: str  # 上次累计的文本
    prev_tool_calls: list[dict]  # 上次累计的 tool_calls 记录 (含 status / result)
    prev_reasoning_content: str | None
    prev_reasoning_duration_ms: int | None
    prev_usage: dict
    prev_finish_reason: str  # "aborted" / "partial_steps" / ...
    prev_status: str  # DB 中曾经的 status (aborted/partial)


@dataclass
class RunResult:
    """AgentRunner.run() 完成后的累计结果.

    finalizer 用它决定:
      - DB message 状态 ("done" | "error" | "aborted" | "partial")
      - SSE 终态事件 ("done" | "error" | "task_partial")
      - 是否 publish turn.completed
    """

    finish_reason: str = "stop"
    # "stop"            正常完成
    # "aborted"         用户 /chat/stop
    # "error"           不可恢复错误
    # "length"          模型 finish_reason=length (上下文截断, 罕见)
    # "partial_steps"   step safety net 抵达 (R5 加)
    # "partial_tokens"  token 预算抵达 (R5 加)
    # "partial_timeout" wall clock 抵达 (R5 加)

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_content: str | None = None
    reasoning_duration_ms: int | None = None
    usage: dict = field(default_factory=dict)
    error_message: str | None = None  # finish_reason="error" 时填

    @property
    def is_partial(self) -> bool:
        return self.finish_reason.startswith("partial_")

    @property
    def is_resumable(self) -> bool:
        """前端能给 [继续生成] 按钮的状态.

        aborted (用户主动停) + partial_* (系统软上限触发) 都可恢复.
        """
        return self.finish_reason == "aborted" or self.is_partial

    @property
    def is_terminal_ok(self) -> bool:
        """LLM 真正自然完成 (才发 turn.completed, 才触发摘要等后置).

        partial / aborted 不算 -- 用户可能要继续, 这时摘要在错误的位置.
        """
        return self.finish_reason == "stop"
