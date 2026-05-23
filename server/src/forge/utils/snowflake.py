"""Snowflake ID 生成器 — 64 位分布式唯一 ID。

结构: [41bit 时间戳 ms][10bit worker][12bit 序列号]
- 41bit 时间戳: ~69 年寿命 (从 EPOCH 起算)
- 10bit worker: 最多 1024 个节点
- 12bit 序列号: 单节点每秒最多 4096 个 ID

纯 Python 实现，零外部依赖。ID 单调递增，天然有序，适合 MySQL 聚簇索引和 cursor-based 分页。
"""

from __future__ import annotations

import os
import threading
import time

# 项目起始时间: 2023-11-14 22:13:20 UTC
EPOCH_MS = 1700000000000
WORKER_BITS = 10
SEQUENCE_BITS = 12
MAX_WORKER_ID = (1 << WORKER_BITS) - 1   # 1023
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1   # 4095
TIMESTAMP_SHIFT = WORKER_BITS + SEQUENCE_BITS  # 22


def _default_worker_id() -> int:
    raw = os.environ.get("FORGE_WORKER_ID", "0").strip()
    if not raw:
        return 0
    try:
        wid = int(raw)
    except ValueError:
        return 0
    return max(0, min(wid, MAX_WORKER_ID))


class SnowflakeGenerator:
    """线程安全的 Snowflake ID 生成器。

    Usage:
        gen = SnowflakeGenerator(worker_id=1)
        new_id = gen.next_id()  # -> int, 18-19 位
    """

    def __init__(self, worker_id: int | None = None) -> None:
        if worker_id is None:
            worker_id = _default_worker_id()
        if not (0 <= worker_id <= MAX_WORKER_ID):
            raise ValueError(f"worker_id 超出范围 [0, {MAX_WORKER_ID}]")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_ms = 0
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            now = int(time.time() * 1000)
            if now == self._last_ms:
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    # 当前 ms 序列耗尽，等下一 ms
                    now = self._wait_next_ms(self._last_ms)
            elif now < self._last_ms:
                # 时钟回拨：等追上上一次的时间戳
                now = self._wait_next_ms(self._last_ms - 1)
            else:
                self._sequence = 0
            self._last_ms = now
            return (
                ((now - EPOCH_MS) << TIMESTAMP_SHIFT)
                | (self._worker_id << SEQUENCE_BITS)
                | self._sequence
            )

    def _wait_next_ms(self, last_ms: int) -> int:
        now = int(time.time() * 1000)
        while now <= last_ms:
            time.sleep(0.0001)  # 100μs
            now = int(time.time() * 1000)
        return now

    # ---- 反向解析 (调试用) ----
    @staticmethod
    def extract_timestamp(snowflake_id: int) -> int:
        """从 ID 中提取毫秒时间戳 (epoch 后 ms)。"""
        return (snowflake_id >> TIMESTAMP_SHIFT) + EPOCH_MS

    @staticmethod
    def extract_worker(snowflake_id: int) -> int:
        """从 ID 中提取 worker ID。"""
        return (snowflake_id >> SEQUENCE_BITS) & MAX_WORKER_ID


# 全局默认实例
_default_gen = SnowflakeGenerator()


def new_snowflake_id() -> int:
    """生成下一个 Snowflake ID（使用默认 worker_id）。"""
    return _default_gen.next_id()
