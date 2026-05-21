"""JsonlLog 单元测试.

覆盖:
- append / append_many 写入与编码 (中文 + sort_keys + \\n)
- iter_all 异步迭代 + 文件不存在视为空
- corrupt 行跳过且只 warning, 不抛
- tail 返回末尾 N 条且顺序保留
- iter_after 按 predicate 切分, 不含匹配项本身
- count 与上述一致
- fsync 开启时仍正常工作 (不验证落盘, 只验证不崩)
- 同 path 并发写串行 (无内容交错)
- 不同 path 互不阻塞
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forge.infrastructure.jsonl import JsonlLog

pytestmark = pytest.mark.asyncio


# ────────────────────── 写 / 读基本流 ──────────────────────


async def test_append_then_iter_all(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "a.jsonl")
    await log.append({"type": "msg", "content": "你好"})
    await log.append({"type": "msg", "content": "world", "n": 2})

    records = [r async for r in log.iter_all()]
    assert records == [
        {"type": "msg", "content": "你好"},
        {"type": "msg", "content": "world", "n": 2},
    ]


async def test_encoding_invariants(tmp_path: Path) -> None:
    """中文不转义 + sort_keys + \\n 行尾."""
    path = tmp_path / "b.jsonl"
    log = JsonlLog(path)
    await log.append({"b": 2, "a": 1, "中文": "值"})

    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    # sort_keys: a 在 b 之前
    text = raw.decode("utf-8")
    assert text.index('"a"') < text.index('"b"')
    # 中文不转义
    assert "中文" in text
    # 不能有 \\u 转义形式
    assert "\\u4e2d" not in text


async def test_append_many(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "c.jsonl")
    await log.append_many([{"i": 1}, {"i": 2}, {"i": 3}])

    records = [r async for r in log.iter_all()]
    assert [r["i"] for r in records] == [1, 2, 3]


async def test_append_many_empty_is_noop(tmp_path: Path) -> None:
    """空列表不应创建文件 (避免空 fd 操作)."""
    path = tmp_path / "d.jsonl"
    log = JsonlLog(path)
    await log.append_many([])
    # parent 在 __init__ 已创建, 文件本身不应存在
    assert not path.exists()


# ────────────────────── 异常 / 边界 ──────────────────────


async def test_iter_all_missing_file_yields_nothing(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "nope.jsonl")
    records = [r async for r in log.iter_all()]
    assert records == []


async def test_corrupt_line_is_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "corrupt.jsonl"
    path.write_text(
        '{"ok": 1}\nthis is not json\n{"ok": 2}\n',
        encoding="utf-8",
    )
    log = JsonlLog(path)

    with caplog.at_level("WARNING"):
        records = [r async for r in log.iter_all()]

    assert records == [{"ok": 1}, {"ok": 2}]
    assert any("JSONL 单行解析失败" in rec.message for rec in caplog.records)


async def test_blank_lines_ignored(tmp_path: Path) -> None:
    path = tmp_path / "blanks.jsonl"
    path.write_text('{"a":1}\n\n\n{"a":2}\n', encoding="utf-8")
    log = JsonlLog(path)
    records = [r async for r in log.iter_all()]
    assert records == [{"a": 1}, {"a": 2}]


async def test_parent_dir_auto_created(tmp_path: Path) -> None:
    """嵌套不存在的子目录, JsonlLog 应自动 mkdir."""
    path = tmp_path / "deep" / "nested" / "x.jsonl"
    log = JsonlLog(path)
    await log.append({"ok": True})
    assert path.exists()


# ────────────────────── tail / iter_after / count ──────────────────────


async def test_tail_returns_last_n(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "t.jsonl")
    for i in range(10):
        await log.append({"i": i})

    last3 = await log.tail(3)
    assert [r["i"] for r in last3] == [7, 8, 9]


async def test_tail_more_than_total_returns_all(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "t2.jsonl")
    await log.append({"i": 1})
    await log.append({"i": 2})

    result = await log.tail(100)
    assert [r["i"] for r in result] == [1, 2]


async def test_tail_zero_returns_empty(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "t3.jsonl")
    await log.append({"i": 1})
    assert await log.tail(0) == []
    assert await log.tail(-1) == []


async def test_iter_after_skips_until_first_match(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "ev.jsonl")
    for i in range(5):
        await log.append({"id": f"evt_{i}"})

    result = [r async for r in log.iter_after(lambda r: r["id"] == "evt_2")]
    # 匹配项本身不返回, 只返回其 *之后* 的
    assert [r["id"] for r in result] == ["evt_3", "evt_4"]


async def test_iter_after_no_match_yields_nothing(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "ev2.jsonl")
    await log.append({"id": "a"})
    await log.append({"id": "b"})

    result = [r async for r in log.iter_after(lambda r: r["id"] == "missing")]
    assert result == []


async def test_count(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "ct.jsonl")
    assert await log.count() == 0

    for _ in range(7):
        await log.append({"x": 1})
    assert await log.count() == 7


async def test_count_skips_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "ct2.jsonl"
    path.write_text('{"ok":1}\nbroken\n{"ok":2}\n', encoding="utf-8")
    log = JsonlLog(path)
    assert await log.count() == 2


# ────────────────────── 并发 / fsync ──────────────────────


async def test_fsync_enabled_does_not_crash(tmp_path: Path) -> None:
    """fsync=True 时 append/append_many 仍正常完成."""
    log = JsonlLog(tmp_path / "f.jsonl", fsync=True)
    await log.append({"a": 1})
    await log.append_many([{"a": 2}, {"a": 3}])

    records = [r async for r in log.iter_all()]
    assert [r["a"] for r in records] == [1, 2, 3]


async def test_concurrent_append_same_path_no_interleave(tmp_path: Path) -> None:
    """同 path 多协程并发 append, 字节级不交错 (每行完整)."""
    log = JsonlLog(tmp_path / "concurrent.jsonl")

    async def worker(label: str, n: int) -> None:
        for i in range(n):
            # 每条 record 含一个长字符串, 增加 interleave 风险
            await log.append({"label": label, "i": i, "pad": "x" * 200})

    await asyncio.gather(
        worker("A", 30),
        worker("B", 30),
        worker("C", 30),
    )

    # 验证每行都能被解析 (说明没字节交错)
    records = [r async for r in log.iter_all()]
    assert len(records) == 90
    # 三 label 各 30 条
    by_label = {"A": 0, "B": 0, "C": 0}
    for r in records:
        by_label[r["label"]] += 1
    assert by_label == {"A": 30, "B": 30, "C": 30}


async def test_concurrent_different_paths_independent(tmp_path: Path) -> None:
    """不同 path 的 lock 互不影响 — 两个文件应能并发跑完."""
    log_a = JsonlLog(tmp_path / "x.jsonl")
    log_b = JsonlLog(tmp_path / "y.jsonl")

    async def write(log: JsonlLog, label: str) -> None:
        for i in range(50):
            await log.append({"label": label, "i": i})

    await asyncio.gather(write(log_a, "A"), write(log_b, "B"))

    a_records = [r async for r in log_a.iter_all()]
    b_records = [r async for r in log_b.iter_all()]
    assert len(a_records) == 50
    assert len(b_records) == 50
    assert all(r["label"] == "A" for r in a_records)
    assert all(r["label"] == "B" for r in b_records)
