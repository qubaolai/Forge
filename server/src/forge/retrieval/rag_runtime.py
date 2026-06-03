"""按系统绑定动态组装 RAG 运行时组件。"""

from __future__ import annotations

import threading

from forge.retrieval.bound_model_resolver import get_bound_model_resolver


class RagRuntime:
    def __init__(self, *, settings, bm25_store) -> None:
        self.settings = settings
        self.bm25_store = bm25_store
        self._vector_stores: dict[str, object] = {}
        self._lock = threading.Lock()

    async def resolve_embedding(self):
        return await get_bound_model_resolver().resolve("rag_embedding")

    async def vector_store_for(self, embedder):
        if embedder is None:
            return None
        model_id = str(embedder._forge_model_id)  # type: ignore[attr-defined]
        with self._lock:
            if model_id in self._vector_stores:
                return self._vector_stores[model_id]
        from forge.retrieval.stores.vector.factory import VectorStoreFactory

        config = dict(self.settings.vector_store.active_config())
        base = config.get("collection_name", "child_chunks")
        config["collection_name"] = f"{base}_m_{model_id}"
        store = VectorStoreFactory.create(self.settings.vector_store.provider, config)
        with self._lock:
            self._vector_stores[model_id] = store
        return store

    async def build_retriever(self):
        from forge.retrieval.factory import RetrieverFactory

        embedder = await self.resolve_embedding()
        vector_store = await self.vector_store_for(embedder)
        reranker = await get_bound_model_resolver().resolve("rag_reranker")
        return RetrieverFactory.create(
            settings=self.settings,
            child_store=vector_store,
            bm25_store=self.bm25_store,
            embedder=embedder,
            reranker=reranker,
        )


_runtime: RagRuntime | None = None


def set_rag_runtime(runtime: RagRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_rag_runtime() -> RagRuntime:
    if _runtime is None:
        raise RuntimeError("RAG runtime 未初始化")
    return _runtime
