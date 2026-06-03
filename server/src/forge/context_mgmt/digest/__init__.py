"""context_mgmt.digest: 会话内容引用化 (digest) 子系统.

归属说明 (架构原则):
    digest 是「当前会话内、支撑当下推理」的短期信息, 属于 context, 不属于 memory.
    本子系统只读真相源 (chat_messages.content), 不依赖 memory 模块。

阶段 1 (止血):
    - DigestPolicy: 读时把单条超长消息折叠为「引用占位 + 摘录」, 防止一条长消息
      挤爆 dialogue 预算或在 _trim_history_by_budget 触发 break 丢弃全部历史。
    - 无缓存命中时降级为 **同步结构化骨架兜底** (复用 segmenter + code_skeleton +
      prose_skeleton, 带 anchor+行号, 标记 digest_pending), 而非旧版盲截断。

阶段 2 (增强, 见计划):
    - 新表 message_digests + 异步 prose_summarizer / code_skeleton 计算无损 digest。
    - read_message / get_artifact(line_range) 按需回读。

降级职责收敛 (修订 E):
    - 单条超长一律先 digest 折叠 (保留信息、可回读); MessageAssembler 的「硬丢弃」
      (history_truncated_by_budget) 退为「折叠后整体仍超 budget」的最后兜底。
    - 折叠使被挤掉的历史变少, ThresholdTrigger (依赖 history dropped)
      触发频率随之下降, 属预期非异常。
"""

from __future__ import annotations

from forge.context_mgmt.digest.policy import DigestApplyResult, DigestPolicy
from forge.context_mgmt.digest.types import (
    DigestLookup,
    DigestRecord,
    Segment,
    SegmentKind,
)

__all__ = [
    "DigestPolicy",
    "DigestApplyResult",
    "DigestRecord",
    "Segment",
    "SegmentKind",
    "DigestLookup",
]
