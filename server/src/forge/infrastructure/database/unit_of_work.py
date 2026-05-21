# """Unit of Work:事务边界统一入口。

# 1. Repository 按需获取:
#    - uow.get_repo(DocumentRepository)   (通用入口,新增 Repo 不改本类)
# 2. **读写分流**:
#    - UnitOfWork(db) 写事务,正常退出 commit
#    - UnitOfWork.read_only(db) 只读事务,退出永远 rollback
# 3. commit() / flush() 显式方法,允许 with 块内手动控制
# 4. 加状态校验, 防止重复进入

# 为什么要有 read_only?
#     检索流程的 _fetch_parents 是纯只读查询,走写事务语义不对、
#     也让 MySQL 跑了不必要的 commit。read_only 显式声明意图,
#     退出时 rollback(对纯查询无副作用,但保证 session 状态干净)。

# 为什么 Repository 写成 get_repo(cls)?
#     遵循开闭原则。原实现里每新增一个 Repository,UoW 都要加一个
#     属性、一个 _xxx 字段、__enter__/__exit__ 里加初始化和清理,
#     UoW 作为公共组件不应频繁变动。改成注册表式按需创建后,
#     UoW 完全稳定,业务侧只需 uow.get_repo(SomeRepository) 即可。
# """
# from __future__ import annotations

# import logging
# from typing import TYPE_CHECKING, Type, TypeVar

# from forge.infrastructure.database.database import Database
# from forge.infrastructure.database.repositories.base import BaseRepository

# if TYPE_CHECKING:
#     from sqlalchemy.orm import Session

# logger = logging.getLogger(__name__)

# R = TypeVar("R", bound=BaseRepository)


# class UnitOfWork:
#     """事务边界 + Repository 容器。

#     必须通过 with 语句使用。Repository 通过 get_repo(cls) 按需获取,
#     同一 UoW 实例内同类型 Repository 复用同一实例。
#     """

#     def __init__(self, db: Database, *, read_only: bool = False):
#         self._db = db
#         self._read_only = read_only
#         self._session: "Session | None" = None
#         self._repositories: dict[type, BaseRepository] = {}
#         self._entered = False

#     # ------------------------------------------------------------------
#     # 工厂方法
#     # ------------------------------------------------------------------
#     @classmethod
#     def read_only(cls, db: Database) -> "UnitOfWork":
#         """构造一个只读 UoW。

#         语义:
#             - 退出时永远 rollback(无论正常/异常)
#             - 用于纯查询,避免无意义的 commit
#             - 仍然提供完整 Repository,但调用 commit() / flush() 会拒绝
#         """
#         return cls(db, read_only=True)

#     # ------------------------------------------------------------------
#     # 上下文管理
#     # ------------------------------------------------------------------
#     def __enter__(self) -> "UnitOfWork":
#         if self._entered:
#             raise RuntimeError("UnitOfWork 不能重复进入")
#         self._session = self._db.new_session()
#         self._repositories = {}
#         self._entered = True
#         return self

#     def __exit__(self, exc_type, exc_val, exc_tb) -> None:
#         if self._session is None:
#             return
#         try:
#             if self._read_only or exc_type is not None:
#                 # 只读 / 异常:回滚
#                 self._session.rollback()
#                 if exc_type is not None and not self._read_only:
#                     logger.warning(
#                         "UnitOfWork rollback due to %s: %s",
#                         exc_type.__name__, exc_val,
#                     )
#             else:
#                 # 写事务正常结束:提交
#                 self._session.commit()
#         finally:
#             self._session.close()
#             self._session = None
#             self._repositories = {}
#             self._entered = False

#     # ------------------------------------------------------------------
#     # 显式控制(可选用,with 块内调用)
#     # ------------------------------------------------------------------
#     def commit(self) -> None:
#         """显式提交。read_only UoW 拒绝调用。"""
#         self._ensure_active()
#         if self._read_only:
#             raise RuntimeError("read_only UoW 不允许 commit")
#         self._session.commit()  # type: ignore[union-attr]

#     def flush(self) -> None:
#         """把待写入的对象 flush 到 DB,但不 commit。

#         用途:写入后想立刻拿到 DB 生成的字段(如自增主键、server_default)。
#         read_only UoW 也允许 flush(只对内存中的 ORM 状态生效)。
#         """
#         self._ensure_active()
#         self._session.flush()  # type: ignore[union-attr]

#     def rollback(self) -> None:
#         """显式回滚。"""
#         self._ensure_active()
#         self._session.rollback()  # type: ignore[union-attr]

#     # ------------------------------------------------------------------
#     # Repository 访问
#     # ------------------------------------------------------------------
#     def get_repo(self, repo_cls: Type[R]) -> R:
#         """按类型获取 Repository。

#         - 必须在 with 块内调用
#         - 同一 UoW 实例内,同一 Repository 类返回同一实例(复用 session)
#         - 新增 Repository 时无需修改 UoW
#         """
#         self._ensure_active()
#         if repo_cls not in self._repositories:
#             self._repositories[repo_cls] = repo_cls(self._session)  # type: ignore[arg-type]
#         return self._repositories[repo_cls]  # type: ignore[return-value]

#     @property
#     def session(self) -> "Session":
#         """裸 session。仅在需要执行非 Repository 覆盖的 SQL 时使用。"""
#         self._ensure_active()
#         return self._session  # type: ignore[return-value]

#     @property
#     def is_read_only(self) -> bool:
#         return self._read_only

#     # ------------------------------------------------------------------
#     # 内部
#     # ------------------------------------------------------------------
#     def _ensure_active(self) -> None:
#         if not self._entered or self._session is None:
#             raise RuntimeError("UnitOfWork 必须在 with 块内使用")
