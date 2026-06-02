"""ORM 模型集合 — 导入即触发 SQLAlchemy 注册。"""

from forge.infrastructure.database.orm.api_key_orm import UserApiKey
from forge.infrastructure.database.orm.auth import RefreshTokenBlacklist
from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.chat_message_orm import ChatMessageOrm
from forge.infrastructure.database.orm.chat_session_orm import ChatSessionOrm
from forge.infrastructure.database.orm.kb_document_chunk_orm import (
    KbDocumentChunkOrm,
)
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm
from forge.infrastructure.database.orm.message_digest_orm import MessageDigestOrm
from forge.infrastructure.database.orm.model_orm import ModelOrm
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
from forge.infrastructure.database.orm.provider_key_orm import ProviderKeyOrm
from forge.infrastructure.database.orm.session_summary_orm import SessionSummaryOrm
from forge.infrastructure.database.orm.user_orm import UserOrm

__all__ = [
    "Base",
    "ChatMessageOrm",
    "ChatSessionOrm",
    "KnowledgeBaseOrm",
    "KbDocumentChunkOrm",
    "KbDocumentOrm",
    "MessageDigestOrm",
    "ModelOrm",
    "ProviderKeyOrm",
    "ProviderOrm",
    "RefreshTokenBlacklist",
    "SessionSummaryOrm",
    "UserApiKey",
    "UserOrm",
]
