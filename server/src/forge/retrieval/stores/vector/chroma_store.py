"""ChromaDB 实现的子块向量存储."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import chromadb
from chromadb.api.types import (
    Documents,
    EmbeddingFunction,
    Embeddings,
    Include,
    Metadatas,
    Where,
)
from chromadb.config import Settings

from forge.core.types import Chunk, ChunkType

from .base import ChildVectorStore, VectorHit
from .factory import register_vector_store

logger = logging.getLogger(__name__)


class _NoopEmbeddingFunction(EmbeddingFunction):
    """占位 EF: 我们的写入路径总是显式传 embeddings, 永远走不到 __call__.

    chromadb 1.x 默认 EF 会自动下载 sentence-transformers 模型, 显式传一个
    占位 EF 避免误下载. __init__ / name / get_config 是 1.x 对子类的硬性要求
    (1.x 把 collection 配置持久化, 没实现这些方法会触发 DeprecationWarning).
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "noop"

    def get_config(self) -> dict:
        return {}

    @classmethod
    def build_from_config(cls, config: dict) -> _NoopEmbeddingFunction:
        return cls()

    def __call__(self, input: Documents) -> Embeddings:
        raise RuntimeError("NoopEF 不应被调用")


@register_vector_store("chroma")
class ChromaChildVectorStore(ChildVectorStore):
    def __init__(self, config: dict):
        if "persist_dir" not in config:
            raise ValueError("ChromaChildVectorStore 需要 config['persist_dir']")

        persist_dir = Path(config["persist_dir"])
        persist_dir.mkdir(parents=True, exist_ok=True)
        collection_name = config.get("collection_name", "child_chunks")

        logger.info("初始化 Chroma client: %s", persist_dir)
        self.client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
                is_persistent=True,
            ),
        )

        try:
            self.collection = self.client.get_collection(
                name=collection_name,
                embedding_function=_NoopEmbeddingFunction(),
            )
        except Exception:
            self.collection = self.client.create_collection(
                name=collection_name,
                embedding_function=_NoopEmbeddingFunction(),
                metadata={"hnsw:space": "cosine"},
            )
        logger.info("Chroma 集合就绪, 文档数=%d", self.collection.count())

    # ==================================================================
    # 写入
    # ==================================================================
    def add_children(
        self,
        children: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        if not children:
            return
        if len(children) != len(embeddings):
            raise ValueError(
                f"children ({len(children)}) 与 embeddings ({len(embeddings)}) 数量不一致"
            )

        BATCH = 50
        total = len(children)

        for start in range(0, total, BATCH):
            end = min(start + BATCH, total)
            batch_children = children[start:end]
            batch_embeddings = embeddings[start:end]

            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict] = []

            for ch in batch_children:
                if ch.chunk_type != ChunkType.CHILD:
                    raise ValueError(f"非子块 chunk: {ch.chunk_id}")
                embed_text = f"{ch.header_path}\n\n{ch.content}" if ch.header_path else ch.content
                ids.append(ch.chunk_id)
                documents.append(embed_text)
                metadatas.append(
                    {
                        "parent_chunk_id": ch.parent_id or "",
                        "document_id": ch.doc_id,  # = kb_documents.id
                        "kb_id": ch.kb_id or "",
                        "header_path": ch.header_path or "",
                        "source_type": ch.source_type,
                    }
                )

            logger.info("Chroma upsert 批次 %d-%d / %d", start, end, total)
            self.collection.upsert(
                ids=ids,
                embeddings=cast(Embeddings, batch_embeddings),
                documents=documents,
                metadatas=cast(Metadatas, metadatas),
            )

        logger.info("Chroma upsert 完成, 共 %d 条", total)

    # ==================================================================
    # 删除 / 统计
    # ==================================================================
    def delete_by_doc(self, doc_id: str) -> int:
        existing = self.collection.get(where={"document_id": doc_id}, include=[])
        ids = existing.get("ids") or []
        n = len(ids)
        if n > 0:
            self.collection.delete(where={"document_id": doc_id})
            logger.debug("Chroma 删除 doc %s 子块 %d 个", doc_id, n)
        return n

    def count_by_doc(self, doc_id: str) -> int:
        result = self.collection.get(where={"document_id": doc_id}, include=[])
        return len(result.get("ids") or [])

    # ==================================================================
    # 检索
    # ==================================================================
    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[VectorHit]:
        if top_k <= 0:
            return []

        # 安全语义: 空白名单严格返回空, 不视为不过滤
        if doc_id_filter is not None and len(doc_id_filter) == 0:
            return []

        where: Where | None = None
        if doc_id_filter:
            # Chroma 的 $in 过滤下推到向量库内部, 不做事后过滤.
            # cast: Chroma 的 Where 是 TypedDict, 字面 dict 推断不窄, 运行时形态正确
            where = cast(Where, {"document_id": {"$in": list(doc_id_filter)}})

        result = self.collection.query(
            query_embeddings=cast(Embeddings, [query_embedding]),
            n_results=top_k,
            where=where,
            include=cast(Include, ["metadatas", "distances"]),
        )

        # Chroma 返回二维结构 (一个 query 对应一行), 取第 0 行
        ids_list = (result.get("ids") or [[]])[0]
        metas_list = (result.get("metadatas") or [[]])[0]
        dist_list = (result.get("distances") or [[]])[0]

        hits: list[VectorHit] = []
        for chunk_id, meta, distance in zip(ids_list, metas_list, dist_list, strict=False):
            meta_dict = dict(meta) if meta else {}
            # metadata 字段的类型 stub 是 str | int | float | SparseVector | list,
            # 我们在 add_children 里只会写入 str, 这里显式转 str 让静态检查通过
            parent_id = str(meta_dict.get("parent_chunk_id", "") or "")
            doc_id = str(meta_dict.get("document_id", "") or "")
            # TODO: 仅 cosine distance 在此公式下能给出 [0, 1] 风格分数;
            #       若未来支持 L2 / IP 距离, 需要按 collection 的 hnsw:space
            #       元数据分发归一化逻辑
            # 保持单调性即可: distance 越小 -> score 越大
            score = 1.0 - float(distance)
            hits.append(
                VectorHit(
                    chunk_id=str(chunk_id),
                    parent_id=parent_id,
                    doc_id=doc_id,
                    score=score,
                )
            )
        return hits
