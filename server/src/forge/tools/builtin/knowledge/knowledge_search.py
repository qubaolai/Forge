"""knowledge_search 工具: 让 ReAct Agent 在用户指定的知识库中检索片段.

调用链:
    ReActAgent.stream → ToolExecutor.aexecute → KnowledgeSearchTool.arun
      → 复用全局 DB session (跑在主 event loop, 共享 lifespan engine)
      → KnowledgeBaseRepository.find_accessible_by_names (权限过滤)
      → retrieval.search.search_chunks (与 /kb/{id}/search 检索测试共用核心)
      → 拼成带 [N] 角标的文本返回给 LLM, 同时把结构化 citations 写入回合收集器

设计要点:
    - 权限: kb_ids / kb_names 由 LLM 给出, 但所有 KB 必须通过 user_id 鉴权.
      工具内部读 current_user_id() ContextVar, ReActAgent.stream 调
      aexecute 时跑在同一个 task 里, ContextVar 自然继承.
    - 引用: 命中片段编号 [1][2]..., 文本里提示 LLM 在回答处用 [编号] 标注;
      同时把结构化来源 append 到 citation 收集器, 由 chat 回合发 SSE + 落库.
    - 计费: embedder/reranker 内部自带 check_budget + record. 超额抛
      LLMBudgetExceeded → 这里 catch 后转友好文本.
    - 实现 arun 而非 run: DB 走全局 async engine, 必须在创建它的 event loop
      上使用. 异步原生入口天然在主 loop 上跑.
"""

from __future__ import annotations

import logging
from typing import Any

from forge.core.request_context import (
    add_citations,
    current_allowed_knowledge_kb_ids,
    current_user_id,
)
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.knowledge_base_repo import (
    KnowledgeBaseRepository,
)
from forge.llm.cost_tracker import LLMBudgetExceeded
from forge.retrieval.common.excerpt import build_query_focused_excerpt
from forge.retrieval.rendering import render_parent_snippet
from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

# 回灌预算兜底 (读不到 settings 时用). 实际值以 settings.retrieval 为准.
_DEFAULT_RECALL_CONTEXT_MAX_TOKENS = 6000
_DEFAULT_SNIPPET_MIN_TOKENS = 400
# 落 citation 的片段摘要长度 (前端来源卡片展示用, 比给 LLM 的更短, 字符数即可).
_CITATION_CONTENT_CHARS = 500


def _as_clean_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list | tuple | set):
        items = list(value)
    else:
        items = [value]
    return [str(item).strip() for item in items if str(item).strip()]


def _parse_top_n(value: Any) -> int | None:
    try:
        top_n = int(value or 5)
    except (TypeError, ValueError):
        return None
    return max(1, min(top_n, 10))


def _format_page_range(page: int | None, page_start: int | None, page_end: int | None) -> str:
    start = page_start if page_start is not None else page
    end = page_end if page_end is not None else start
    if start is None:
        return ""
    if end is None or end == start:
        return f"第 {start} 页"
    return f"第 {start}-{end} 页"


@register_tool
class KnowledgeSearchTool(Tool):
    """在指定知识库中检索相关文档片段."""

    name = "knowledge_search"
    description = (
        "在用户可访问的知识库 (KB) 中检索与问题相关的文档片段. "
        "当用户告知需要查询知识库时调用. "
        "优先使用系统提示中列出的 kb_id 作为 kb_ids; kb_names 仅为兼容字段, "
        "禁止臆造. 返回最相关的若干段落原文 + 来源标识 (每段带 [编号]), "
        "回答时请基于片段内容作答, 并在引用处用 [编号] 标注来源."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索问题, 通常是用户问题的核心或其改写",
            },
            "kb_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "兼容字段: 知识库名称列表。优先使用 kb_ids",
            },
            "kb_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "知识库 ID 列表，取自系统提示里的 kb_id",
            },
            "top_n": {
                "type": "integer",
                "description": "最多返回片段数, 默认 5, 上限 10",
                "default": 5,
            },
        },
        "required": ["query", "kb_ids"],
    }
    parallelism_safe = True

    async def arun(self, args: dict[str, Any]) -> str:
        query = (args.get("query") or "").strip()
        kb_names = _as_clean_str_list(args.get("kb_names"))
        kb_ids = _as_clean_str_list(args.get("kb_ids"))
        top_n = _parse_top_n(args.get("top_n"))

        if not query:
            return "错误: query 不能为空"
        if not kb_ids and not kb_names:
            return "错误: kb_ids 不能为空, 必须明确指定要搜哪些知识库"
        if top_n is None:
            return "错误: top_n 必须是整数"

        allowed_kb_ids = set(current_allowed_knowledge_kb_ids())
        if not allowed_kb_ids:
            return "错误: 当前对话未选择知识库, 不允许执行 knowledge_search"
        if kb_names:
            return "错误: 当前对话由前端选择知识库, 请只使用系统提示中列出的 kb_ids"
        requested = set(kb_ids)
        disallowed = sorted(requested - allowed_kb_ids)
        if disallowed:
            return f"错误: 请求的知识库不在本轮允许范围内: {disallowed}"

        user_id = current_user_id() or ""
        if not user_id:
            logger.warning("knowledge_search 调用缺少 user_id, 拒绝执行")
            return "错误: 未识别到当前用户身份, 无法搜索知识库"

        try:
            return await self._do_search(query, kb_ids, kb_names, top_n, user_id)
        except LLMBudgetExceeded as e:
            logger.warning("knowledge_search 预算超额: %s", e)
            return f"错误: 当前用户的调用额度已耗尽, 无法继续检索 ({e})"
        except Exception as e:  # noqa: BLE001
            logger.exception("knowledge_search 执行失败")
            return f"错误: 知识库检索失败: {e}"

    async def _do_search(
        self,
        query: str,
        kb_ids: list[str],
        kb_names: list[str],
        top_n: int,
        user_id: str,
    ) -> str:
        from forge.retrieval.search import search_chunks

        async with session_scope() as db:
            kb_repo = KnowledgeBaseRepository(db)
            kbs_by_id = await kb_repo.find_accessible_by_ids(kb_ids, user_id)
            kbs_by_name = await kb_repo.find_accessible_by_names(kb_names, user_id)
            by_id = {str(kb.id): kb for kb in [*kbs_by_id, *kbs_by_name]}
            kbs = list(by_id.values())

            found_ids = {str(kb.id) for kb in kbs_by_id}
            missing_ids = [i for i in kb_ids if i not in found_ids]
            found_names = {kb.name for kb in kbs_by_name}
            missing_names = [n for n in kb_names if n not in found_names]
            if missing_ids or missing_names:
                # 出于安全性, 不区分"不存在"与"无权限", 一律返回访问不到
                return (
                    f"知识库无法访问或不存在: "
                    f"ids={missing_ids or []}, names={missing_names or []}. "
                    f"已访问 ID: {sorted(by_id) or '无'}"
                )

            kb_ids = [str(kb.id) for kb in kbs]
            results = await search_chunks(db, kb_ids=kb_ids, query=query, top_n=top_n)
            if not results:
                names = sorted(kb.name for kb in kbs)
                return f"在知识库 {names} 中未找到与 '{query}' 相关的内容"

            total_budget, min_budget = self._resolve_snippet_budget()
            return self._format_results(
                results,
                query=query,
                total_budget_tokens=total_budget,
                min_snippet_tokens=min_budget,
            )

    @staticmethod
    def _resolve_snippet_budget() -> tuple[int, int]:
        """读取回灌 token 预算, settings 不可用时用兜底默认."""
        try:
            from forge.config.settings import get_settings

            rcfg = get_settings().retrieval
            return int(rcfg.recall_context_max_tokens), int(rcfg.snippet_min_tokens)
        except Exception:  # noqa: BLE001
            return _DEFAULT_RECALL_CONTEXT_MAX_TOKENS, _DEFAULT_SNIPPET_MIN_TOKENS

    @staticmethod
    def _format_results(
        results,
        query: str = "",
        *,
        total_budget_tokens: int = _DEFAULT_RECALL_CONTEXT_MAX_TOKENS,
        min_snippet_tokens: int = _DEFAULT_SNIPPET_MIN_TOKENS,
    ) -> str:
        """格式化结果给 LLM, 并把结构化 citations 写入回合收集器.

        每段以 [N] 起头并携带引用信息 (文档名 / KB / 章节 / 页码), 便于 LLM
        在最终答复里用 [N] 标注来源; citations 走 SSE 渲染为前端来源卡片.

        回灌预算: 总预算 total_budget_tokens 均分到各片段 (每条不低于
        min_snippet_tokens); 片段数超出预算可容纳数时, 按 final_score 保留
        前若干条 (results 已按分数降序), 丢弃的条数在开头提示.
        """
        # 预算分配: 尽量给每条 min 预算, 不够则按分数只保留能装下的前 K 条
        max_snippets = max(1, total_budget_tokens // max(1, min_snippet_tokens))
        kept = list(results[:max_snippets])
        dropped = len(results) - len(kept)
        per_budget = (
            max(min_snippet_tokens, total_budget_tokens // len(kept))
            if kept
            else min_snippet_tokens
        )

        header = f"共检索到 {len(results)} 条相关片段"
        if dropped > 0:
            header += f"（因篇幅仅展示相关度最高的 {len(kept)} 条，省略 {dropped} 条）"
        citations: list[dict] = []
        lines: list[str] = [header + ". 回答时请在引用处用 [编号] 标注来源:\n"]
        for i, r in enumerate(kept, 1):
            content = (r.content or "").strip()
            snippet = render_parent_snippet(r, query, budget_tokens=per_budget)

            # 引用信息: 优先人类可读字段, 没有再回退
            citation_parts: list[str] = []
            if r.document_name:
                citation_parts.append(f"文档《{r.document_name}》")
            if r.kb_name:
                citation_parts.append(f"知识库「{r.kb_name}」")
            if r.header_path:
                citation_parts.append(f"章节: {r.header_path}")
            page_label = _format_page_range(r.page, r.page_start, r.page_end)
            if page_label:
                citation_parts.append(page_label)
            head = " | ".join(citation_parts) if citation_parts else "片段"
            lines.append(f"[{i}] {head} (相关度={r.final_score:.4f})")
            if r.source_url:
                lines.append(f"来源: {r.source_url}")
            lines.append(f"(document_id={r.document_id} chunk_id={r.chunk_id})")
            lines.append(f"内容:\n{snippet}")
            lines.append("")

            metadata = {
                "kb_name": r.kb_name or "",
                "header_path": r.header_path or "",
                "source_url": r.source_url,
            }
            if r.page is not None:
                metadata["page"] = r.page
            if r.page_start is not None:
                metadata["page_start"] = r.page_start
            if r.page_end is not None:
                metadata["page_end"] = r.page_end

            citations.append(
                {
                    "index": i,
                    "chunk_id": str(r.chunk_id),
                    "document_id": str(r.document_id),
                    "document_name": r.document_name or "",
                    "content": build_query_focused_excerpt(
                        content,
                        query,
                        max_chars=_CITATION_CONTENT_CHARS,
                    ),
                    "score": round(float(r.final_score), 4),
                    "metadata": metadata,
                }
            )

        add_citations(citations)
        return "\n".join(lines).rstrip()
