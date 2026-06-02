"""context_mgmt.digest: 会话内容引用化 (digest) 子系统.

归属说明 (架构原则):
    digest 是「当前会话内、支撑当下推理」的短期信息, 属于 context, 不属于 memory.
    本子系统只读真相源 (chat_messages.content), 不依赖 memory 模块。

阶段 1 (止血):
    - DigestPolicy: 读时把单条超长消息折叠为「引用占位 + 摘录」, 防止一条长消息
      挤爆 dialogue 预算或在 _trim_history_by_budget 触发 break 丢弃全部历史。
    - 无缓存命中时降级为廉价同步截断 (标记 digest_pending)。

阶段 2 (增强, 见计划):
    - 新表 message_digests + 异步 prose_summarizer / code_skeleton 计算无损 digest。
    - read_message / get_artifact(line_range) 按需回读。
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
