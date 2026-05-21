"""JSONL 文件读写底层助手 — 单机化改造的核心存储原语.

对应方案文档:``单机化改造方案.md`` 第六节 (JSONL Helper API 设计).

服务对象:

- ``sessions/{id}.jsonl``      — 对话历史 (append + iter_all + tail)
- ``cost.jsonl``               — LLM 调用流水
- ``audit.jsonl``              — 危险工具调用审计
- ``workflows/wf_*/events.jsonl`` — workflow 事件流 (append + iter_after)
- ``facts.jsonl`` (P3+)        — 长期事实

仅做底层 bytes ↔ dict 翻译; **不做** 聚合 / 索引 / schema 校验,
那些由各调用方在自己的 wrapper (SessionLog / CostLog / ...) 里完成.

不变量
-------
写:
- ``encoding="utf-8"`` / ``ensure_ascii=False`` (中文可读)
- 行尾固定 ``\\n`` (规避 Win CRLF)
- ``json.dumps(sort_keys=True)`` (git diff 友好)
- ``parent.mkdir(parents=True, exist_ok=True)`` 自动创建父目录
- 同 ``path`` 多协程串行写 (共享 asyncio.Lock); 不同 path 互不阻塞

读:
- 文件不存在 → 当作空 (不抛)
- 单行 JSON 解析失败 → 跳过 + ``logger.warning``, 继续
- 不要求 ``type`` / ``ts`` 等字段, helper 对 schema 无感
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import weakref
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiofiles

logger = logging.getLogger(__name__)

__all__ = ["JsonlLog", "atomic_write_text"]


# 同 path 全局共享一把 asyncio.Lock; key 用绝对路径字符串
# weakref 字典: 没人引用某 path 的 lock 时自动回收 (避免常驻一堆锁)
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = asyncio.Lock()


async def _lock_for(path: Path) -> asyncio.Lock:
    """返回 ``path`` 专属的 asyncio.Lock, 同 path 跨调用方共享同一把.

    用 WeakValueDictionary 自动回收无引用的锁.
    """
    key = str(path.resolve())
    async with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


class JsonlLog:
    """JSONL 文件读写器.

    单进程内安全 (asyncio.Lock 保护并发协程);
    跨进程并发安全由应用级 filelock 保证 (单 assistant 进程假设).

    Parameters
    ----------
    path:
        JSONL 文件路径. 父目录自动创建.
    fsync:
        是否在每次 ``append`` 后调 ``fsync``. 默认 ``False`` (性能优先).
        关键场景 (workflow 事件流, 不能丢) 显式置 ``True``.
    """

    def __init__(self, path: Path, *, fsync: bool = False) -> None:
        self.path = Path(path)
        self._fsync = fsync
        # 提前 mkdir, 避免每次写都判断
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ────────────────────── 写 ──────────────────────

    async def append(self, record: dict[str, Any]) -> None:
        """追加单条记录, 原子.

        ``record`` 必须 JSON 可序列化. 同 path 并发写按 asyncio.Lock 串行.
        """
        line = self._encode(record)
        lock = await _lock_for(self.path)
        async with lock, aiofiles.open(self.path, mode="a", encoding="utf-8") as f:
            await f.write(line)
            if self._fsync:
                await f.flush()
                # aiofiles 没有暴露 fsync, 借底层 fd
                os.fsync(f.fileno())

    async def append_many(self, records: list[dict[str, Any]]) -> None:
        """批量追加 — 共享一次锁 + 一次 fsync, 减少开销.

        空列表直接返回, 不打开文件.
        """
        if not records:
            return
        payload = "".join(self._encode(r) for r in records)
        lock = await _lock_for(self.path)
        async with lock, aiofiles.open(self.path, mode="a", encoding="utf-8") as f:
            await f.write(payload)
            if self._fsync:
                await f.flush()
                os.fsync(f.fileno())

    # ────────────────────── 读 ──────────────────────

    async def iter_all(self) -> AsyncIterator[dict[str, Any]]:
        """异步逐行 yield 解析后的 dict.

        文件不存在视为空. 单行解析失败跳过并 ``warning`` (不抛).
        """
        if not self.path.exists():
            return
        async with aiofiles.open(self.path, encoding="utf-8") as f:
            line_no = 0
            async for raw in f:
                line_no += 1
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "JSONL 单行解析失败 file=%s line=%d err=%s",
                        self.path,
                        line_no,
                        exc,
                    )
                    continue

    async def tail(self, n: int) -> list[dict[str, Any]]:
        """返回最后 ``n`` 条记录 (按文件顺序, 末尾优先).

        简单实现: 全文读取后切片. 当前 < 50MB 文件 100ms 内完成,
        够本工具用. 若 ``cost.jsonl`` / ``audit.jsonl`` 累积超过 50MB
        且 ``tail`` 调用频繁, 再换反向 seek 实现.

        # TODO(perf): 若文件 > 50MB 且 tail 是热路径, 改成从尾部 seek 读 chunk.
        """
        if n <= 0:
            return []
        records: list[dict[str, Any]] = []
        async for r in self.iter_all():
            records.append(r)
        return records[-n:]

    async def iter_after(
        self, predicate: Callable[[dict[str, Any]], bool]
    ) -> AsyncIterator[dict[str, Any]]:
        """从首条满足 ``predicate`` 的记录 **之后** 开始 yield.

        例如 workflow reattach 拿到 ``from_event_id`` 后:

            async for evt in events.iter_after(lambda r: r["id"] == from_event_id):
                yield evt

        如果没有任何记录满足 predicate, 不 yield 任何东西.
        如果有多条满足, 以 **首条匹配** 为分界 (后续全部 yield).
        """
        matched = False
        async for r in self.iter_all():
            if matched:
                yield r
                continue
            if predicate(r):
                matched = True

    # ────────────────────── 工具方法 ──────────────────────

    async def count(self) -> int:
        """返回有效记录数 (跳过空行 / corrupt 行).

        全文扫一遍, 大文件慎用.
        """
        total = 0
        async for _ in self.iter_all():
            total += 1
        return total

    # ────────────────────── 内部 ──────────────────────

    @staticmethod
    def _encode(record: dict[str, Any]) -> str:
        """统一序列化规则 — sort_keys + ensure_ascii=False + 固定 \\n."""
        return json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"


async def atomic_write_text(path: Path, content: str) -> None:
    """原子写文本: 先写同目录临时文件 + fsync, 再 ``os.replace``.

    崩溃后 ``path`` 要么保留旧版本, 要么是完整新版本, 绝不会半截.
    与 ``_lock_for`` 共享 per-path asyncio.Lock, 同一 path 多协程串行写.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    lock = await _lock_for(path)
    async with lock:
        async with aiofiles.open(tmp, mode="w", encoding="utf-8") as f:
            await f.write(content)
            await f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
