"""全局 Retriever 访问点.

为什么需要:
    knowledge_search 工具是无状态注册的, 拿不到 FastAPI 的 app.state. 工具
    需要一个稳定入口拿到启动期装配好的 retriever. 这里用进程级单例: lifespan
    启动末尾调 set_retriever(...), 工具调用时 get_retriever().

线程安全:
    set_retriever / get_retriever 写读单个引用, GIL 保证原子性, 不加锁.
    set_retriever 一般只在启动期被调用一次, 不存在并发写场景.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import ParentChildRetriever


_retriever: ParentChildRetriever | None = None


def set_retriever(retriever: ParentChildRetriever) -> None:
    """注册全局 retriever. 由 api/lifespan.py 启动末尾调用."""
    global _retriever
    _retriever = retriever


def get_retriever() -> ParentChildRetriever:
    """业务侧获取 retriever. 未初始化时抛 RuntimeError."""
    if _retriever is None:
        raise RuntimeError("Retriever 未初始化, 检查 lifespan 启动是否成功 (RAG extras 是否安装)")
    return _retriever


def is_ready() -> bool:
    return _retriever is not None


def reset() -> None:
    """测试用: 清空全局 retriever."""
    global _retriever
    _retriever = None
