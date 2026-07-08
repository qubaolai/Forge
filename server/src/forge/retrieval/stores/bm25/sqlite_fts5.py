"""SQLite FTS5 实现的 BM25 倒排索引.

设计要点:
    - 独立 SQLite 文件, 不复用 MySQL, 因此与 ingest_pipeline 的 UnitOfWork
      解耦, 由 pipeline 编排顺序 (与 Chroma 同等地位)
    - FTS5 schema:
        * 4 个 ID 字段标 UNINDEXED, 不入倒排索引, 仅作行数据存储
        * tokens 列是唯一参与全文索引的列, 内容是 jieba 切完后空格连接
        * tokenize='unicode61' 让 FTS5 按空白二次切分, 与外部 jieba 配合
    - 并发模型: 单连接 + threading.Lock
        * SQLite 写本来就是单文件锁串行, 单连接 + Lock 满足绝大多数场景
        * WAL 模式让读不被写阻塞, 检索 QPS 不会被入库锁住
    - bm25() 函数返回负值 (越小越相关), 对外统一翻转为 "越大越相关"
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import cast

from forge.core.types import Chunk, ChunkType
from forge.retrieval.common.tokenizer import Tokenizer

from .base import BM25Hit, BM25Store
from .factory import register_bm25_store

logger = logging.getLogger(__name__)


# FTS5 MATCH 语法保留字符: " * + - ( ) : ^ NEAR
# 对每个 token 用双引号包裹, 转义内部双引号 (FTS5 中 "" 表示字面量 ")
def _quote_phrase(token: str) -> str:
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


@register_bm25_store("sqlite_fts5")
class SqliteFTS5BM25Store(BM25Store):
    def __init__(self, config: dict):
        super().__init__(config)

        if "db_path" not in config:
            raise ValueError("SqliteFTS5BM25Store 需要 config['db_path']")
        if "_tokenizer" not in config:
            raise ValueError("SqliteFTS5BM25Store 需要注入 tokenizer (走 BM25StoreFactory.create)")

        self.db_path = Path(config["db_path"])
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.table = config.get("table_name", "bm25_chunks")
        self.tokenizer: Tokenizer = config["_tokenizer"]

        # check_same_thread=False 允许跨线程使用同一连接, 配合 _lock 保证安全.
        # SQLite 本身的写锁在文件层, Python 这层用 Lock 串行化 cursor 操作.
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit, 由我们手动管理事务
        )
        self._lock = threading.Lock()

        self._init_schema()
        logger.info(
            "SqliteFTS5BM25Store 就绪: path=%s table=%s count=%d",
            self.db_path,
            self.table,
            self._count_all(),
        )

    # ==================================================================
    # schema 初始化
    # ==================================================================
    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            # WAL: 读不阻塞写, 写不阻塞读 (除 checkpoint 外)
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self.table} USING fts5(
                    chunk_id UNINDEXED,
                    parent_chunk_id UNINDEXED,
                    document_id UNINDEXED,
                    kb_id UNINDEXED,
                    tokens,
                    tokenize='unicode61 remove_diacritics 2'
                );
            """)
            # FTS5 不支持 CREATE INDEX, 但 doc_id 等 UNINDEXED 列在
            # WHERE 子句中是全表扫描. 数据量大时可考虑加 contentless 表
            # 配合外部 SQLite 表索引, 当前规模 (单库百万级以内) 无需.

    # ==================================================================
    # 写入
    # ==================================================================
    def add_children(self, children: list[Chunk]) -> None:
        if not children:
            return

        # 预先做 chunk_type 校验, 避免事务中途失败
        for ch in children:
            if ch.chunk_type != ChunkType.CHILD:
                raise ValueError(f"非子块 chunk: {ch.chunk_id}")

        # 预先在锁外分词 (CPU 密集), 减少持锁时间
        rows = []
        for ch in children:
            embed_text = f"{ch.header_path}\n\n{ch.content}" if ch.header_path else ch.content
            tokens_str = self.tokenizer.tokenize_to_string(embed_text)
            rows.append(
                (
                    ch.chunk_id,
                    ch.parent_id or "",
                    ch.doc_id,  # = kb_documents.id
                    ch.kb_id or "",
                    tokens_str,
                )
            )

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN;")
                # FTS5 没有唯一约束, 用 delete + insert 实现 upsert.
                # 同次调用内若有重复 chunk_id 也能正确覆盖.
                chunk_ids = [r[0] for r in rows]
                # 分批 IN 删除, 避免 SQL 长度爆炸
                BATCH = 500
                for i in range(0, len(chunk_ids), BATCH):
                    batch = chunk_ids[i : i + BATCH]
                    placeholders = ",".join(["?"] * len(batch))
                    cur.execute(
                        f"DELETE FROM {self.table} WHERE chunk_id IN ({placeholders});",
                        batch,
                    )
                cur.executemany(
                    f"INSERT INTO {self.table} "
                    f"(chunk_id, parent_chunk_id, document_id, kb_id, tokens) "
                    f"VALUES (?, ?, ?, ?, ?);",
                    rows,
                )
                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise

        logger.info("BM25 写入子块 %d 条", len(rows))

    # ==================================================================
    # 删除
    # ==================================================================
    def delete_by_doc(self, doc_id: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN;")
                cur.execute(
                    f"DELETE FROM {self.table} WHERE document_id = ?;",
                    (doc_id,),
                )
                n = cur.rowcount or 0
                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise
        if n > 0:
            logger.debug("BM25 删除 doc %s 子块 %d 个", doc_id, n)
        return n

    # ==================================================================
    # 统计
    # ==================================================================
    def count_by_doc(self, doc_id: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM {self.table} WHERE document_id = ?;",
                (doc_id,),
            )
            return cast(int, cur.fetchone()[0])

    def _count_all(self) -> int:
        cur = self._conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {self.table};")
        return cast(int, cur.fetchone()[0])

    # ==================================================================
    # 检索
    # ==================================================================
    def search(
        self,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[BM25Hit]:
        if not query or top_k <= 0:
            return []
        if doc_id_filter is not None and len(doc_id_filter) == 0:
            return []

        # 用与入库一致的 tokenizer 切 query, 保证 token 空间一致
        tokens = self.tokenizer.tokenize(query)
        if not tokens:
            return []

        # FTS5 默认是 AND 连接, 任一 token 不命中整个 query 命中 0 条.
        # BM25 多词查询的语义是 OR (任一 token 命中即可, 命中越多分越高),
        # 所以这里显式用 " OR " 连接.
        match_expr = " OR ".join(_quote_phrase(t) for t in tokens)

        sql = (
            f"SELECT chunk_id, parent_chunk_id, document_id, bm25({self.table}) AS raw_score "
            f"FROM {self.table} "
            f"WHERE {self.table} MATCH ? "
        )
        params: list = [match_expr]

        # document_id 过滤下推到 SQL, 不做事后过滤
        if doc_id_filter:
            placeholders = ",".join(["?"] * len(doc_id_filter))
            sql += f"AND document_id IN ({placeholders}) "
            params.extend(doc_id_filter)

        sql += f"ORDER BY bm25({self.table}) ASC LIMIT ?;"
        params.append(top_k)

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                # 常见原因: MATCH 语法被特殊字符撑坏. 已用 phrase quote 兜底,
                # 仍出现则记录 query 便于排查, 返回空结果而非抛出.
                logger.exception("BM25 search 失败, query=%r match=%r", query, match_expr)
                return []

        hits: list[BM25Hit] = []
        for rank, (chunk_id, parent_id, doc_id, raw_score) in enumerate(rows, start=1):
            # FTS5 bm25() 返回越小越相关 (实际是负值). 翻转为越大越相关.
            score = -float(raw_score)
            hits.append(
                BM25Hit(
                    chunk_id=chunk_id,
                    parent_id=parent_id,
                    doc_id=doc_id,
                    score=score,
                    rank=rank,
                )
            )
        return hits

    # ==================================================================
    # 资源释放
    # ==================================================================
    def close(self) -> None:
        with self._lock:
            self._conn.close()
