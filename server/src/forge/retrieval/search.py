"""KB 检索核心: 把 query + kb_ids 解析成 RetrievedParent 列表.

knowledge_search 工具与 KB 详情页检索测试端点共用本函数, 避免两份 runtime
装配 / doc_id 过滤逻辑漂移.

边界:
    - 不做 KB 权限判定. 调用方 (工具 / 路由) 先完成 KB 鉴权, 把允许检索的
      kb_ids 传进来.
    - 不做结果格式化. 返回原始 RetrievedParent, 由调用方按场景渲染
      (工具拼 LLM 文本 + citations; 检索端点转 KbSearchHit).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.repositories.kb_document_repo import (
    KbDocumentRepository,
)
from forge.retrieval.base import RetrievedParent


async def search_chunks(
    db: AsyncSession,
    *,
    kb_ids: list[str],
    query: str,
    top_n: int,
) -> list[RetrievedParent]:
    """在指定 KB 集合内混合检索, 返回结构化父块片段.

    kb_ids 为空 / 所选 KB 无已索引文档时返回 []; 检索本身无命中也返回 [].
    """
    if not kb_ids:
        return []
    doc_repo = KbDocumentRepository(db)
    doc_ids = await doc_repo.list_indexed_doc_ids(kb_ids)
    if not doc_ids:
        return []

    from forge.retrieval.rag_runtime import get_rag_runtime

    runtime = get_rag_runtime()
    embedder = await runtime.resolve_embedding()
    # 向量召回仅限「当前绑定模型已就绪」的文档, 避免跨模型向量混检
    vector_doc_ids = (
        await doc_repo.list_vector_ready_doc_ids(
            kb_ids, str(cast(Any, embedder)._forge_model_id)
        )
        if embedder is not None
        else []
    )
    retriever = await runtime.build_retriever()
    return await retriever.retrieve(
        query=query,
        session=db,
        doc_id_filter=doc_ids,
        vector_doc_id_filter=vector_doc_ids,
        top_n=top_n,
    )
