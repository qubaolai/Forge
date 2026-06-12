"""FactExtractionWatermark ORM — 事实抽取水位表 (一 session 一行).

为什么独立成表而不挂在 user_facts 上: 抽取结果可能全部被去重 Skip 或
LLM 抽不出事实, 此时也必须推进水位, 否则同一段对话每 N 轮重复送 LLM
白烧成本。水位语义与 session_summaries.covered_until_message_id 一致:
"该消息及之前的内容已被抽取过"。
"""

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class FactExtractionWatermarkOrm(Base, BigIntPKMixin):
    __tablename__ = "fact_extraction_watermarks"

    session_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, comment="→ chat_sessions.id (雪花)"
    )
    last_message_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="已抽取到哪条消息为止 (→ chat_messages.id 雪花)"
    )

    __table_args__ = table_args(
        comment="事实抽取水位表 (一 session 一行, 原地 upsert)",
    )
