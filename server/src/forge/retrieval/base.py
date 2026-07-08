"""检索流程配置与对外 DTO.

设计点:
    - RetrievalConfig 是流程控制配置 (top_k / top_m / top_n 等), 与 provider
      子配置无关; 用 dataclass 而非 pydantic, 让 retriever 不耦合配置框架,
      装配代码 (RetrieverFactory) 负责把 settings 映射进来
    - RetrievedParent 是对外统一返回 DTO, 调用方一般只看 final_score
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalConfig:
    """检索流程参数.

    Attributes:
        top_n_parent:        最终返回父块数
        top_m_for_rerank:    送 rerank 的父块数 (仅在 rerank 启用时生效)
        vector_top_k:        向量召回子块 top_k
        bm25_top_k:          BM25 召回子块 top_k
        rerank_enabled:      rerank 流程开关. 与 reranker 实例存在性正交:
                             reranker 实例可以存在但 enabled=False 时跳过流程.
        vector_enabled:      向量召回路实际是否启用.
        bm25_enabled:        BM25 召回路实际是否启用.
    """

    top_n_parent: int = 5
    top_m_for_rerank: int = 20
    vector_top_k: int = 30
    bm25_top_k: int = 30
    rerank_enabled: bool = True
    vector_enabled: bool = True
    bm25_enabled: bool = True

    def __post_init__(self):
        if self.top_n_parent <= 0:
            raise ValueError(f"top_n_parent 必须 > 0, 收到 {self.top_n_parent}")
        if self.rerank_enabled and self.top_m_for_rerank < self.top_n_parent:
            raise ValueError(
                f"top_m_for_rerank ({self.top_m_for_rerank}) 必须 >= "
                f"top_n_parent ({self.top_n_parent})"
            )
        if self.vector_enabled and self.vector_top_k <= 0:
            raise ValueError(f"vector_top_k 必须 > 0, 收到 {self.vector_top_k}")
        if self.bm25_enabled and self.bm25_top_k <= 0:
            raise ValueError(f"bm25_top_k 必须 > 0, 收到 {self.bm25_top_k}")


@dataclass
class RetrievedParent:
    """检索结果 DTO. 对外统一接口.

    final_score 是统一对外的排序分, 来源对调用方透明:
        - rerank 启用且成功: final_score == rerank_score
        - rerank 关闭或失败: final_score == fusion_score, rerank_score is None

    保留 fusion_score / rerank_score 双字段, 调用方可做诊断:
        - rerank_score is None 表明走了降级路径

    引用信息字段 (document_name / kb_name / source_url / page) 由 retriever
    在 _fetch_parents 时一次性 JOIN kb_documents + knowledge_bases 填充, 让
    LLM 能引用回原始文档 ("根据 X.pdf 第 N 页 ...").
    """

    # ---- 标识 ----
    chunk_id: str
    document_id: str  # 来自 kb_document_chunks.document_id
    kb_id: str  # 来自 kb_document_chunks.kb_id

    # ---- 引用信息 (面向 LLM 与前端引用面板) ----
    document_name: str = ""  # kb_documents.name (用户可读文件名)
    kb_name: str = ""  # knowledge_bases.name
    source_url: str | None = None  # kb_documents.source_url (web/对象存储等)
    page: int | None = None  # 从 extra.page / metadata 提取
    page_start: int | None = None  # 从 extra.page_start 提取
    page_end: int | None = None  # 从 extra.page_end 提取

    # ---- 内容 / 上下文 ----
    content: str = ""
    header_path: str = ""
    source_type: str = ""

    # ---- 评分 ----
    final_score: float = 0.0
    fusion_score: float = 0.0
    rerank_score: float | None = None

    # ---- 命中诊断 ----
    hit_child_count: int = 0
    hit_chunk_ids: list[str] = field(default_factory=list)

    # ---- 原始扩展字段 (来自 kb_document_chunks.extra) ----
    metadata: dict = field(default_factory=dict)
