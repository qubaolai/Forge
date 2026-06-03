"""context_mgmt.recall: 语义历史召回子系统.

归属说明 (架构原则):
    语义召回是「当前会话内、支撑当下推理」的短期能力, 属于 context, 不属于 memory。
    只读真相源 (chat_messages.content) 算向量缓存, 不依赖 memory 模块。

构成:
    - MessageEmbeddingStore: 消息向量缓存 (message_embeddings 表) 的读写。
    - run_embedding_task: 冷路径 (turn.completed 异步) 为近期消息补算并缓存向量。
    - EmbeddingScorer (在 filters/semantic.py): 读缓存向量 + query 实时 embedding,
      给 HybridFilter 的早期轮次打相似度分。

默认关闭 (settings.context.semantic_recall.enabled=False), 开启需配置 embedding provider。
embedder 不可用时 HybridFilter 保留全部早期消息。
"""

from __future__ import annotations
