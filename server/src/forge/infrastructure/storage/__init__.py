"""存储抽象层.

包含两类抽象:

1. **文件存储 (blob)** — ``FileStorage`` / ``LocalFileStorage``, 给 KB 上传文件
   等大对象用. 本地磁盘 V1, 对象存储 (OSS / S3) V2 扩展点.

2. **结构化数据存储 (data)** — ``data_protocols.py`` 定义 7 个 Protocol
   (SessionStore / MessageStore / CostStore / AuditStore / SummaryStore /
   KnowledgeBaseStore / KbDocumentStore), ``data_factory.py`` 按
   ``deployment_mode`` 装配本地实现. S6.5 阶段仅支持 ``local`` 模式.

业务代码 (chat / memory / api / kb) 只 import 本模块的 Protocol 与 factory,
不再 import 具体 ``*Repository`` 类.
"""

from forge.infrastructure.storage.base import FileStorage, StoredFile
from forge.infrastructure.storage.data_factory import (
    get_deployment_mode,
    make_audit_store,
    make_cost_store,
    make_kb_document_store,
    make_knowledge_base_store,
    make_message_store,
    make_session_store,
    make_summary_store,
)
from forge.infrastructure.storage.data_protocols import (
    AuditStore,
    CostStore,
    KbDocumentStore,
    KnowledgeBaseStore,
    MessageStore,
    SessionStore,
    SummaryStore,
)
from forge.infrastructure.storage.local_fs import LocalFileStorage

__all__ = [
    # blob 文件存储
    "FileStorage",
    "StoredFile",
    "LocalFileStorage",
    # 结构化数据 Protocol
    "SessionStore",
    "MessageStore",
    "CostStore",
    "AuditStore",
    "SummaryStore",
    "KnowledgeBaseStore",
    "KbDocumentStore",
    # 装配工厂
    "make_session_store",
    "make_message_store",
    "make_cost_store",
    "make_audit_store",
    "make_summary_store",
    "make_knowledge_base_store",
    "make_kb_document_store",
    "get_deployment_mode",
]
