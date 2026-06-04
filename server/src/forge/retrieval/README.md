# `forge.retrieval` — RAG 与检索

文档解析 → 切分 → 向量化 → 存储 → 检索的完整链路。配合知识库（KB）子系统，给 `knowledge_search` 工具供数。

## 设计理念

1. **延迟重依赖**：`__init__.py` 只 re-export 轻量 DTO/ABC；重型组件（RetrieverFactory / 向量库 / BM25）由调用方按需 import，避免不需要 RAG 的进程（Celery worker / 普通 api 路由）被 chromadb 全套依赖拖累。
2. **软降级装配**：RAG 组件在 `lifespan` 启动阶段动态装配，某层不可用时优雅降级，不阻塞主应用。
3. **Parent-Child 检索**：向量库存子块、回灌父块，兼顾召回精度与上下文完整。向量 + BM25 双路融合。
4. **策略可插拔**：parser / chunker / embedder / store / reranker / fusion 各成一层 ABC，按配置组合。

## 模块速览

```
retrieval/
├── base.py / factory.py      ← Retriever ABC + RetrieverFactory (组装 ParentChildRetriever)
├── pipeline.py               ← 检索流水线
├── rag_runtime.py            ← RAG 运行时
├── parsers/                  ← pdf / word / text 解析
├── chunkers/                 ← 切分策略 (hierarchical 等)
├── embedders/                ← 向量化
├── stores/vector + stores/bm25 ← ChildVectorStore(Chroma) + BM25Store(sqlite_fts5)
├── rerankers/ + fusion/      ← 重排 + 多路融合
├── recall/                   ← 召回
├── bound_model_resolver.py   ← 解析 rag_embedding 绑定模型
└── rebuild_tasks.py          ← 索引重建后台任务
```

> 文档入库由 `api/services/kb_ingest_service.py` 以 Saga 风格编排：`parsing → chunking → embedding → persist → indexed`，失败补偿清理并标 `failed`。

## 如何使用

```python
# 重型组件按需 import (不要从包顶层)
from forge.retrieval.factory import RetrieverFactory
retriever = RetrieverFactory.create(...)
results = await retriever.retrieve(query, ...)
```

业务层一般不直接用，通过 `knowledge_search` 工具间接调用。

## 如何扩展

- **新文件类型**：在 `parsers/` 实现 parser 并注册到 dispatcher。
- **新切分/向量化/重排策略**：实现对应 ABC（`chunkers/` / `embedders/` / `rerankers/`）。
- **新向量/全文后端**：实现 `stores/vector` 或 `stores/bm25` 的 store 接口。

## 边界与注意

- 不要从 `forge.retrieval` 顶层 import 重型组件（会拉 chromadb）。
- DB 只存 `storage_path` 键；文件落 `LocalFileStorage`（`{kb_id}/{doc_id}/{filename}`）。
