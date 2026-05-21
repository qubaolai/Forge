"""分类轨迹收集器.

debug=True 时记录每个段落的判定过程, 最后落盘为 JSON.
不规范文档调参时可以快速定位 "这条为什么没识别成标题".
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """单条段落的判定轨迹."""

    index: int
    text_preview: str  # 文本前 60 字
    text_length: int
    bold: bool
    font_size: float | None
    original_label: str | None
    decision: str  # title / text
    level: int | None
    source: str  # native_style / rule:<n> / heuristic / fallback
    candidates: list[str] = field(default_factory=list)
    matched_rule: str | None = None
    score: float | None = None
    score_breakdown: dict = field(default_factory=dict)


class ClassifyTracer:
    """判定轨迹收集器.

    调用 record(...) 累积, 最后 dump(path) 落盘.

    Attributes:
        enabled: 是否启用. 关闭时所有方法都是 no-op.
        entries: 累积的轨迹列表.
    """

    def __init__(self, enabled: bool = False):
        """初始化."""
        self.enabled = enabled
        self.entries: list[TraceEntry] = []

    def record(
        self,
        index: int,
        text: str,
        bold: bool,
        font_size: float | None,
        original_label: str | None,
        decision: str,
        level: int | None,
        source: str,
        candidates: list[str] | None = None,
        matched_rule: str | None = None,
        score: float | None = None,
        score_breakdown: dict | None = None,
    ) -> None:
        """记录一次判定."""
        if not self.enabled:
            return
        self.entries.append(
            TraceEntry(
                index=index,
                text_preview=text[:60],
                text_length=len(text),
                bold=bold,
                font_size=font_size,
                original_label=original_label,
                decision=decision,
                level=level,
                source=source,
                candidates=candidates or [],
                matched_rule=matched_rule,
                score=score,
                score_breakdown=score_breakdown or {},
            )
        )

    def dump(self, path: Path, doc_id: str = "") -> None:
        """落盘为 JSON.

        Args:
            path: 输出文件路径.
            doc_id: 文档 ID, 写入文件头便于查找.
        """
        if not self.enabled or not self.entries:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "doc_id": doc_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(self.entries),
            "title_count": sum(1 for e in self.entries if e.decision == "title"),
            "entries": [asdict(e) for e in self.entries],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("debug 轨迹已写入 %s (%d 条)", path, len(self.entries))
