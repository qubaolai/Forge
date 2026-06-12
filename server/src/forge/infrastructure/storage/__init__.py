"""存储抽象层。

包含两类抽象:

1. **文件存储 (blob)** — ``FileStorage`` / ``LocalFileStorage``, 给 KB 上传文件
   等大对象用. 本地磁盘 V1, 对象存储 (OSS / S3) V2 扩展点.

2. **结构化数据存储 (data)** — ``data_protocols.py`` 定义 8 个 ABC
   (SessionStore / MessageStore / CostStore / AuditStore / SummaryStore /
   FactStore / KnowledgeBaseStore / KbDocumentStore) 及共享数据视图
   (SessionView / ChatMessageView).

业务代码 (chat / memory / api / kb) 只 import 本模块的 ABC 与视图,
不 import 具体 ``*Repository`` 类.
"""

from forge.infrastructure.storage.base import FileStorage, StoredFile
from forge.infrastructure.storage.content_store import (
    ContentSlice,
    ContentStore,
    DbMessageContentStore,
)
from forge.infrastructure.storage.data_protocols import (
    AuditStore,
    ChatMessageView,
    CostStore,
    FactStore,
    KbDocumentStore,
    KnowledgeBaseStore,
    MessageStore,
    SessionStore,
    SessionView,
    SummaryStore,
)
from forge.infrastructure.storage.local_fs import LocalFileStorage

__all__ = [
    # blob 文件存储
    "FileStorage",
    "StoredFile",
    "LocalFileStorage",
    # 结构化数据 ABC
    "SessionStore",
    "MessageStore",
    "CostStore",
    "AuditStore",
    "SummaryStore",
    "FactStore",
    "KnowledgeBaseStore",
    "KbDocumentStore",
    # 共享数据视图
    "SessionView",
    "ChatMessageView",
    # 会话内容真相源切片 (digest 回读)
    "ContentStore",
    "ContentSlice",
    "DbMessageContentStore",
]
