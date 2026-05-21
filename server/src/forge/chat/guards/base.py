"""LoopGuard 抽象 + 共享数据类型.

每个 Guard 在 ReAct 每一步开始前被问一次. 它可以:
    - 返回 None     : 通过, 不引导
    - 返回 Guidance : 注入 system 消息引导 LLM (severity 决定后果)

severity 语义:
    hint        - 注入提示, 模型按提示自由决定 (eg "你还剩 3 步")
    warning     - 强引导, 同时通常配合丢 tool 选项的暗示
    force_stop  - 这一步强制 tool_choice="none", 模型必须只输出文本

多个 Guard 同时生效时, Runner 把它们的 guidance 合并; 任一是 force_stop ->
本步 force_text_only.

Guards 是无状态的 Protocol; 具体实现可以有内部 state (eg StuckDetector
的滑窗), 但要按 turn 创建新实例避免跨 turn 污染.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Severity = Literal["hint", "warning", "force_stop"]


@dataclass(frozen=True)
class LoopState:
    """ReAct 循环当前快照 (传给 Guard 让它判断).

    Runner 每步构造一个新的 LoopState (而不是给 Guard 维护). Guard 只能读,
    内部 state (eg deque) 由 Guard 自管.
    """

    step_index: int  # 0-based, 当前要执行的第几步
    max_steps: int  # 配置上限 (Runner 设的 safety net, 通常 50+)
    last_tool_name: str | None = None  # 上一步的 tool name (若有多个 tool, 取第一个)
    last_tool_args_hash: str | None = None  # 上一步的 args canonical hash
    last_step_tool_calls: tuple = ()  # 上一步 tool_calls 全集 (R4 看完整列表用)
    accumulated_tokens: int = 0  # R4 token budget 用
    elapsed_seconds: float = 0.0  # R4 wall clock 用


@dataclass(frozen=True)
class Guidance:
    """Guard 决定要引导时的返回值."""

    content: str  # 注入到 messages 的 system 文本
    severity: Severity = "hint"


class LoopGuard(Protocol):
    """LoopGuard 通用接口. 每个 turn 实例化一次, 内部可有 state."""

    async def before_step(self, state: LoopState) -> Guidance | None:
        """每一步 LLM 调用前被问一次.

        Returns:
            Guidance | None: None 表示通过, 否则注入引导.
        """
        ...
