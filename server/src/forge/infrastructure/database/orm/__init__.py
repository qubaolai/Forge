"""ORM 模型集合 - 导入即触发 SQLAlchemy 注册.

单机化改造后, 保留:
    - UserOrm + RefreshTokenBlacklist (auth 登录链路)
    - AgentOrm / Summary / KB (业务核心)

下线 (改 JSONL / config):
    - chat_orm / audit_log_orm / message_feedback_orm / model_endpoint_orm / llm_cost_orm
"""

from forge.infrastructure.database.orm.agent_orm import AgentOrm
from forge.infrastructure.database.orm.auth import RefreshTokenBlacklist
from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.kb_document_chunk_orm import (
    KbDocumentChunkOrm,
)
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm
from forge.infrastructure.database.orm.session_summary_orm import (
    SessionSummaryOrm,
)
from forge.infrastructure.database.orm.user_orm import UserOrm

__all__ = [
    "Base",
    "UserOrm",
    "RefreshTokenBlacklist",
    "AgentOrm",
    "KnowledgeBaseOrm",
    "KbDocumentOrm",
    "KbDocumentChunkOrm",
    "SessionSummaryOrm",
]
