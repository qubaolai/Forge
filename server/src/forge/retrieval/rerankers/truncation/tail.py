"""尾截策略 (默认实现).

行为:
    - 单文档超长 -> 直接 text[:max_chars], DEBUG 单条日志
    - 批次内超过 monitor_threshold (默认 10%) 的文档被截断 -> WARNING 一次

监控的目的:
    截断本身对 rerank 分值有影响 (尤其当父块关键信息靠后时).
    生产环境跑一段时间, 观察 WARNING 频率与父块切分尺寸的关系,
    决定是否升级到 head_tail 或 segment_topk 策略.
"""

from __future__ import annotations

import logging

from .base import TruncationStrategy
from .factory import register_truncation

logger = logging.getLogger(__name__)


@register_truncation("tail")
class TailTruncation(TruncationStrategy):
    """尾截 + 批次级截断率监控."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.monitor_threshold = float(config.get("monitor_threshold", 0.1))

    @property
    def name(self) -> str:
        return "tail"

    def truncate(
        self,
        query: str,
        documents: list[str],
        max_chars: int,
    ) -> list[str]:
        # query 当前未使用, 接口保持完整以适配后续策略
        del query

        if not documents:
            return []

        out: list[str] = []
        truncated_count = 0
        max_seen = 0

        for text in documents:
            length = len(text)
            # TODO: 当前按字符数尾截, 后续可改为 token-aware (基于 tiktoken / 模型自带分词) 或语义保留 (head_tail / segment_topk 策略)
            if length > max_chars:
                truncated_count += 1
                max_seen = max(max_seen, length)
                logger.debug(
                    "rerank 文档超长尾截: %d -> %d 字符",
                    length,
                    max_chars,
                )
                out.append(text[:max_chars])
            else:
                out.append(text)

        # 批次级监控: 超过阈值告警一次
        if truncated_count > 0:
            ratio = truncated_count / len(documents)
            if ratio >= self.monitor_threshold:
                logger.warning(
                    "rerank 批次截断率告警: %d/%d (%.1f%%) 文档被尾截, "
                    "最长 %d 字符 -> %d. 阈值=%.1f%%. "
                    "若持续出现, 考虑减小父块尺寸或升级截断策略.",
                    truncated_count,
                    len(documents),
                    ratio * 100,
                    max_seen,
                    max_chars,
                    self.monitor_threshold * 100,
                )

        return out
