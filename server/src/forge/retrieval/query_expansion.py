"""HyDE (Hypothetical Document Embeddings) 查询扩展.

思路: 用户 query 往往与文档用词不对齐 (口语 vs 书面). HyDE 先让 LLM 针对
query 写一段"假想答案", 再用假想答案 (可选拼接原 query) 去做向量召回——
假想答案与真实文档在语义/用词上更接近, 提升稠密召回命中率.

边界:
    - 仅作用于向量召回一路; BM25 与 rerank 仍用原始 query.
    - LLM 走网关 utility/fast 档 + 精确缓存 (cache_enabled=True): 同一 query
      重复检索命中缓存, 不重复付费.
    - 任意失败 (网关未就绪 / 超时 / 空返回) 均降级为原始 query, 绝不阻断检索.
"""

from __future__ import annotations

import logging

from forge.llm import LLMGateway, LLMRequest
from forge.llm.providers.base import ChatMessage

logger = logging.getLogger(__name__)

# 假想文档生成 prompt: 要求简洁、只输出答案段落本身
_HYDE_PROMPT = (
    "请针对下面的问题，写一段简洁的、假设性的参考答案段落，用于知识库检索匹配。"
    "只输出答案段落本身，不要复述问题、不要加任何前后缀或解释。\n\n问题：{query}"
)


class HydeGenerator:
    """把 query 扩展为"假想文档 + 原 query"文本, 供向量召回 embed."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_tokens: int = 200,
        concat_original: bool = True,
    ) -> None:
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._concat_original = concat_original

    async def expand(self, query: str) -> str:
        """返回用于向量 embed 的文本. 失败降级为原始 query."""
        query = (query or "").strip()
        if not query:
            return query

        req = LLMRequest(
            messages=[ChatMessage(role="user", content=_HYDE_PROMPT.format(query=query))],
            temperature=0.3,
            max_tokens=self._max_tokens,
            task_type="utility",
            model_profile="fast",
            cache_enabled=True,  # 同一 query 复用假想文档, 不重复付费
        )
        try:
            resp = await self._gateway.complete(req)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HyDE 生成失败, 降级为原始 query: %s", exc)
            return query

        hypothetical = (resp.content or "").strip()
        if not hypothetical:
            return query
        if self._concat_original:
            return f"{hypothetical}\n{query}"
        return hypothetical
