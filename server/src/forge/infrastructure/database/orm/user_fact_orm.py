"""UserFact ORM — 用户级长期事实表 (跨会话记忆).

归属: memory 子系统的 FactStore 持久化后端。写路径由事实抽取任务
(memory.extract_facts) 经 ConflictResolver 决策后落库; 读路径按 user_id
全量拉取 + 暴力余弦召回 (单用户事实量级小, 不引入向量库)。

隔离维度: 仅按 user_id (MemoryScope.for_user)。
source_session_id 记录事实来源会话 (溯源), 为将来 "删会话连带遗忘" 留口;
事实本身不随会话删除。

向量存储: vector_i8 复用 int8 对称量化字节模式 (见 forge.utils.vector);
model 列记录生成向量的 embedding 模型, 模型切换后旧向量按 "未命中" 处理。
"""

from sqlalchemy import BigInteger, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.infrastructure.database.orm.base import Base
from forge.infrastructure.database.orm.mixins import BigIntPKMixin, table_args


class UserFactOrm(Base, BigIntPKMixin):
    __tablename__ = "user_facts"

    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="→ users.id (雪花), 唯一隔离维度"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="事实正文 (如: 用户偏好 Python)"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="llm_extracted",
        comment="来源: llm_extracted / user_manual",
    )
    source_session_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="来源会话 (雪花), 溯源用; 手动添加为 NULL"
    )
    vector_i8: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, comment="int8 对称量化向量字节 (一字节一维); 无 embedder 时为 NULL"
    )
    model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="生成向量的 embedding 模型标识"
    )
    dim: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="向量维度 (= len(vector_i8))"
    )

    __table_args__ = table_args(
        Index("ix_userfact_user", "user_id"),
        Index("ix_userfact_source_session", "source_session_id"),
        comment="用户长期事实表 (user 级跨会话记忆)",
    )
