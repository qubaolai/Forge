"""Retrieval 包.

注意: 这里只 re-export 轻量类型 (DTO / Protocol). 重型组件 (RetrieverFactory /
ParentChildRetriever / 向量/BM25 store) 由调用方按需 import, 避免 import 此包
就把 chromadb 全套依赖拉进来 (尤其在不需要 RAG 的进程, 比如 Celery worker /
api 路由模块 import 时).
"""

from .base import RetrievalConfig, RetrievedParent

__all__ = [
    "RetrievalConfig",
    "RetrievedParent",
]
