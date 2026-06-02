"""digest 计算与存储辅助单测: segmenter / code_skeleton / content_store / ORM 注册."""

from __future__ import annotations

from forge.context_mgmt.digest.code_skeleton import build_code_segment
from forge.context_mgmt.digest.segmenter import split_segments
from forge.infrastructure.storage.content_store import (
    parse_ref,
    payload_to_text,
    slice_text,
)


# ---------------------------------------------------------------------------
# segmenter
# ---------------------------------------------------------------------------
def test_segmenter_splits_prose_and_code_with_line_numbers():
    content = "\n".join([
        "这是说明。",        # 1
        "第二行。",          # 2
        "```python",        # 3 fence open
        "def foo():",       # 4
        "    return 1",     # 5
        "```",              # 6 fence close
        "结尾说明。",        # 7
    ])
    segs = split_segments(content)
    assert [s.kind for s in segs] == ["prose", "code", "prose"]

    prose1, code, prose2 = segs
    assert (prose1.start_line, prose1.end_line) == (1, 2)
    assert code.language == "python"
    assert (code.start_line, code.end_line) == (4, 5)   # 代码体, 不含围栏行
    assert "def foo():" in code.text
    assert prose2.start_line == 7


def test_segmenter_empty_returns_nothing():
    assert split_segments("") == []
    assert split_segments("   \n  ") == []  # 纯空白 prose 不产段


def test_segmenter_unclosed_fence_treated_as_code():
    content = "\n".join(["```js", "const a = 1;", "const b = 2;"])
    segs = split_segments(content)
    assert len(segs) == 1
    assert segs[0].kind == "code"
    assert segs[0].language == "js"
    assert segs[0].start_line == 2  # 代码体从围栏下一行起


# ---------------------------------------------------------------------------
# code_skeleton (无 tree-sitter-language-pack 时走启发式)
# ---------------------------------------------------------------------------
def test_code_skeleton_builds_segment_with_anchor():
    [raw] = [s for s in split_segments("```python\ndef foo():\n    return 1\n```") if s.kind == "code"]
    seg = build_code_segment(raw)
    assert seg.kind == "code"
    assert seg.anchor is not None
    assert "python block" in seg.anchor
    # 启发式降级也应包含代码原文前几行
    assert "def foo():" in seg.digest_text
    assert seg.start_line == raw.start_line and seg.end_line == raw.end_line


# ---------------------------------------------------------------------------
# content_store: parse_ref / slice_text / payload_to_text
# ---------------------------------------------------------------------------
def test_parse_ref_variants():
    assert parse_ref("[ref:msg:abc]") == ("msg", "abc")
    assert parse_ref("ref:msg:abc") == ("msg", "abc")
    assert parse_ref("msg:abc") == ("msg", "abc")
    assert parse_ref("art:x1") == ("art", "x1")
    assert parse_ref("garbage") == ("", "")
    assert parse_ref("doc:zzz") == ("", "")  # 不支持的 kind


def test_slice_text_inclusive_1based():
    content = "a\nb\nc\nd\ne"
    sl = slice_text(content, (2, 4))
    assert sl.text == "b\nc\nd"
    assert sl.total_lines == 5
    assert sl.returned_range == (2, 4)
    assert sl.truncated is True


def test_slice_text_full_when_no_range():
    content = "a\nb\nc"
    sl = slice_text(content, None)
    assert sl.text == content
    assert sl.total_lines == 3
    assert sl.returned_range == (1, 3)
    assert sl.truncated is False


def test_slice_text_clamps_out_of_range():
    content = "a\nb\nc"
    sl = slice_text(content, (2, 999))
    assert sl.text == "b\nc"
    assert sl.returned_range == (2, 3)
    assert sl.truncated is True


def test_payload_to_text_handles_shapes():
    assert payload_to_text("hello") == "hello"
    assert payload_to_text({"content": "X"}) == "X"
    # 无 content 键 -> JSON 序列化
    assert "\"k\"" in payload_to_text({"k": 1}) or "k" in payload_to_text({"k": 1})


# ---------------------------------------------------------------------------
# ORM 注册: create_all 需能建出 message_digests
# ---------------------------------------------------------------------------
def test_message_digest_table_registered():
    import forge.infrastructure.database.orm  # noqa: F401 触发注册
    from forge.infrastructure.database.orm.base import Base

    assert "message_digests" in Base.metadata.tables
    cols = set(Base.metadata.tables["message_digests"].columns.keys())
    assert {"message_id", "session_id", "segments", "source_hash", "status"} <= cols


# ---------------------------------------------------------------------------
# 配置: min_tokens 夹紧到 cap (避免折叠却永不被 digest 的死区)
# ---------------------------------------------------------------------------
def test_digest_settings_clamps_min_tokens_to_cap():
    from forge.config.domains.context import ContextDigestSettings

    cfg = ContextDigestSettings(per_message_token_cap=4000, min_tokens=9000)
    assert cfg.min_tokens == 4000  # 已夹紧

    ok = ContextDigestSettings(per_message_token_cap=8000, min_tokens=2000)
    assert ok.min_tokens == 2000  # 合理配置不动
