"""Phase 7 测试: A/B 路由 (跳过) + 流式缓存回放."""

from __future__ import annotations

from forge.llm.providers.base import ChatChunk
from forge.llm.streaming import replay_as_chunks


def test_replay_empty_content_yields_final_chunk():
    chunks = replay_as_chunks("")
    assert len(chunks) == 1
    assert chunks[0].delta == ""
    assert chunks[0].finish_reason == "stop"


def test_replay_short_content_single_chunk():
    chunks = replay_as_chunks("hi", chunk_size=40)
    assert len(chunks) == 1
    assert chunks[0].delta == "hi"
    assert chunks[0].finish_reason == "stop"


def test_replay_long_content_splits_by_chunk_size():
    content = "x" * 100
    chunks = replay_as_chunks(content, chunk_size=40)
    assert len(chunks) == 3
    assert chunks[0].delta == "x" * 40
    assert chunks[1].delta == "x" * 40
    assert chunks[2].delta == "x" * 20
    # 只有最后 chunk 有 finish_reason
    assert chunks[0].finish_reason is None
    assert chunks[1].finish_reason is None
    assert chunks[2].finish_reason == "stop"


def test_replay_usage_only_on_final_chunk():
    chunks = replay_as_chunks(
        "abcdef",
        chunk_size=2,
        usage={"prompt_tokens": 5, "completion_tokens": 6},
    )
    assert len(chunks) == 3
    assert chunks[0].usage is None
    assert chunks[1].usage is None
    assert chunks[2].usage == {"prompt_tokens": 5, "completion_tokens": 6}


def test_replay_preserves_finish_reason_argument():
    chunks = replay_as_chunks("abc", chunk_size=10, finish_reason="length")
    assert chunks[-1].finish_reason == "length"


def test_replay_aggregated_content_roundtrip():
    """聚合所有 chunk 的 delta 应该等于原 content."""
    original = "hello world! 中文也可以正确切片"
    chunks = replay_as_chunks(original, chunk_size=5)
    rebuilt = "".join(c.delta for c in chunks)
    assert rebuilt == original
