from __future__ import annotations

from forge.retrieval.common.excerpt import build_query_focused_excerpt


def test_query_focused_excerpt_keeps_late_chinese_match_visible() -> None:
    content = "开头说明。" * 80 + "付款条款为验收后 30 天内支付。" + "补充说明。" * 80

    excerpt = build_query_focused_excerpt(content, "付款条款是什么", max_chars=80)

    assert "付款条款为验收后 30 天内支付" in excerpt
    assert excerpt.startswith("...(前文已省略)")
    assert excerpt.endswith("...(后文已截断)")


def test_query_focused_excerpt_falls_back_to_prefix_when_no_query_match() -> None:
    content = "第一段。" * 50 + "第二段。" * 50

    excerpt = build_query_focused_excerpt(content, "不存在的关键词", max_chars=24)

    assert excerpt == content[:24] + "...(已截断)"


def test_query_focused_excerpt_returns_whole_short_content() -> None:
    content = "短内容包含付款条款。"

    assert build_query_focused_excerpt(content, "付款条款", max_chars=100) == content
