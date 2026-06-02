"""digest 子系统的值对象与协议.

设计:
    - Segment:      一条消息切分后的单个分段 (prose / code), 带行号区间 + 锚点 + digest 文本。
    - DigestRecord: 一条消息的完整 digest (多段聚合), 缓存于 message_digests (阶段 2)。
    - DigestLookup: 「按 message_id 取已缓存 digest」的只读源 (阶段 1 恒为空)。

风格约定 (与 context_mgmt/types.py 一致):
    - 不可变值对象用 frozen dataclass。
    - 注释中文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Mapping

# 分段类型: 文章段落 (摘要) 或 代码块 (骨架)
SegmentKind = Literal["prose", "code"]


@dataclass(frozen=True)
class Segment:
    """一条消息切分后的单个分段.

    start_line / end_line: 在原文中的行号区间 (1-based, 闭区间), 供 read_message 定向回读。
    anchor: 定位锚点 (代码: "def login (L42-78)"; 文章: 分段标题), 可空。
    digest_text: 该段的摘要 (prose) 或骨架签名 (code)。
    """

    kind: SegmentKind
    start_line: int
    end_line: int
    digest_text: str
    anchor: str | None = None

    def render(self) -> str:
        """渲染为占位文本中的一段 (含锚点)."""
        head = f"[{self.anchor}]" if self.anchor else f"[L{self.start_line}-{self.end_line}]"
        body = self.digest_text.strip()
        return f"{head}\n{body}" if body else head


@dataclass(frozen=True)
class DigestRecord:
    """一条消息的完整 digest (多段聚合).

    ref: 统一引用格式 (chat: "msg:<message_id>"; CLI: "art:<artifact_id>")。
    total_tokens: 原文 token 估算 (用于展示 / 判定)。
    segments: 有序分段列表。
    """

    ref: str
    total_tokens: int
    segments: tuple[Segment, ...] = ()
    generated_at: datetime | None = None
    model: str | None = None

    def render(self) -> str:
        """拼接所有分段, 形成占位文本主体 (不含 [ref:...] 行)."""
        return "\n\n".join(seg.render() for seg in self.segments)


# 「按 message_id 取已缓存 digest」的只读映射 (阶段 1 传 None / 空 dict)。
# 阶段 2 由 HistoryProvider 批量预取后传入 (避免逐条 DB 查询)。
DigestLookup = Mapping[str, DigestRecord]
