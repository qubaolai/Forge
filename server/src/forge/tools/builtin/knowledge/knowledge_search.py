"""knowledge_search 工具: 让 ReAct Agent 在用户指定的知识库中检索片段.

调用链:
    ReActAgent.stream → ToolExecutor.aexecute → KnowledgeSearchTool.arun
      → 复用全局 DB session (跑在主 event loop, 共享 lifespan engine)
      → KnowledgeBaseRepository.find_accessible_by_names (权限过滤)
      → KbDocumentRepository.list_indexed_doc_ids
      → ParentChildRetriever.retrieve (内部走 embedder/reranker 网关)
      → 拼成纯文本返回给 LLM

设计要点:
    - 权限: kb_names 由 LLM 给出, 但所有 KB 必须通过 user_id 鉴权.
      工具内部读 current_user_id() ContextVar, ReActAgent.stream 调
      aexecute 时跑在同一个 task 里, ContextVar 自然继承.
    - 计费: embedder/reranker 内部自带 check_budget + record. 超额抛
      LLMBudgetExceeded → 这里 catch 后转友好文本.
    - 实现 arun 而非 run: DB 走全局 async engine, 必须在创建它的 event loop
      上使用, 否则会出 "Future attached to a different loop". 异步原生入口
      天然在主 loop 上跑, 不需要起临时 loop.
"""

from __future__ import annotations

import logging
from typing import Any

from forge.core.request_context import current_user_id
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.kb_document_repo import (
    KbDocumentRepository,
)
from forge.infrastructure.database.repositories.knowledge_base_repo import (
    KnowledgeBaseRepository,
)
from forge.llm.cost_tracker import LLMBudgetExceeded
from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

# 单条片段返回给 LLM 时的内容截断 (字符数). 超长会浪费 LLM 上下文.
_MAX_SNIPPET_CHARS = 1200


@register_tool
class KnowledgeSearchTool(Tool):
    """在指定知识库中检索相关文档片段."""

    name = "knowledge_search"
    description = (
        "在用户可访问的知识库 (KB) 中检索与问题相关的文档片段. "
        "当用户告知需要查询知识库时调用. "
        "kb_names 必填, 取值范围限定在系统提示中已列出的可用 KB 列表里, "
        "禁止臆造. 返回最相关的若干段落原文 + 来源标识, "
        "由模型基于片段回答并标注来源."
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
                "description": "知识库名称列表 (取自系统提示的可用 KB 列表), 必填",
            },
            "top_n": {
                "type": "integer",
                "description": "最多返回片段数, 默认 5, 上限 10",
                "default": 5,
            },
        },
        "required": ["query", "kb_names"],
    }
    parallelism_safe = True

    async def arun(self, args: dict[str, Any]) -> str:
        query = (args.get("query") or "").strip()
        kb_names = args.get("kb_names") or []
        top_n = int(args.get("top_n") or 5)
        top_n = max(1, min(top_n, 10))

        if not query:
            return "错误: query 不能为空"
        if not kb_names:
            return "错误: kb_names 不能为空, 必须明确指定要搜哪些知识库"

        user_id = current_user_id() or ""
        if not user_id:
            logger.warning("knowledge_search 调用缺少 user_id, 拒绝执行")
            return "错误: 未识别到当前用户身份, 无法搜索知识库"

        try:
            return await self._do_search(query, kb_names, top_n, user_id)
        except LLMBudgetExceeded as e:
            logger.warning("knowledge_search 预算超额: %s", e)
            return f"错误: 当前用户的调用额度已耗尽, 无法继续检索 ({e})"
        except Exception as e:  # noqa: BLE001
            logger.exception("knowledge_search 执行失败")
            return f"错误: 知识库检索失败: {e}"

    async def _do_search(
        self,
        query: str,
        kb_names: list[str],
        top_n: int,
        user_id: str,
    ) -> str:
        session_factory = get_session_factory()
        async with session_factory() as db:
            kb_repo = KnowledgeBaseRepository(db)
            kbs = await kb_repo.find_accessible_by_names(kb_names, user_id)

            found_names = {kb.name for kb in kbs}
            missing = [n for n in kb_names if n not in found_names]
            if missing:
                # 出于安全性, 不区分"不存在"与"无权限", 一律返回访问不到
                return (
                    f"知识库无法访问或不存在: {missing}. 已访问列表: {sorted(found_names) or '无'}"
                )

            kb_ids = [str(kb.id) for kb in kbs]
            doc_repo = KbDocumentRepository(db)
            doc_ids = await doc_repo.list_indexed_doc_ids(kb_ids)
            if not doc_ids:
                return f"所选知识库 {sorted(found_names)} 中没有已索引的文档, 无可检索内容"

            from forge.retrieval.rag_runtime import get_rag_runtime

            runtime = get_rag_runtime()
            embedder = await runtime.resolve_embedding()
            vector_doc_ids = (
                await doc_repo.list_vector_ready_doc_ids(
                    kb_ids, str(getattr(embedder, "_forge_model_id"))
                )
                if embedder is not None else []
            )
            retriever = await runtime.build_retriever()
            results = await retriever.retrieve(
                query=query,
                session=db,
                doc_id_filter=doc_ids,
                vector_doc_id_filter=vector_doc_ids,
                top_n=top_n,
            )

            if not results:
                return f"在知识库 {sorted(found_names)} 中未找到与 '{query}' 相关的内容"

            return self._format_results(results, kbs_by_id={kb.id: kb for kb in kbs})

    @staticmethod
    def _format_results(results, kbs_by_id: dict) -> str:
        """格式化结果给 LLM. 输出引用信息 (文档名 / KB / 页码) 便于 LLM
        在最终答复里说"根据 X.pdf 第 N 页 …".
        """
        lines: list[str] = [f"共检索到 {len(results)} 条相关片段:\n"]
        for i, r in enumerate(results, 1):
            content = (r.content or "").strip()
            if len(content) > _MAX_SNIPPET_CHARS:
                content = content[:_MAX_SNIPPET_CHARS] + "...(已截断)"

            lines.append(f"[片段 {i}] 相关度={r.final_score:.4f}")
            # 引用信息: 优先用人类可读字段, 没有再回退到 ID
            citation_parts: list[str] = []
            if r.document_name:
                citation_parts.append(f"文档《{r.document_name}》")
            if r.kb_name:
                citation_parts.append(f"知识库「{r.kb_name}」")
            if r.header_path:
                citation_parts.append(f"章节: {r.header_path}")
            if r.page is not None:
                citation_parts.append(f"第 {r.page} 页")
            if citation_parts:
                lines.append(" | ".join(citation_parts))
            if r.source_url:
                lines.append(f"来源: {r.source_url}")
            # 机器 ID 放最后, 给可能需要溯源的工具链路用
            lines.append(f"(document_id={r.document_id} chunk_id={r.chunk_id})")
            lines.append(f"内容:\n{content}")
            lines.append("")
        return "\n".join(lines).rstrip()
