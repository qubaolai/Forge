from __future__ import annotations

from forge.api.services.kb_service import _excerpt_search_hit


def test_kb_search_hit_excerpt_focuses_on_query_match() -> None:
    content = "前置背景。" * 500 + "发票抬头必须与合同主体一致。" + "后续背景。" * 1000

    excerpt = _excerpt_search_hit(content, "发票抬头要求")

    assert "发票抬头必须与合同主体一致" in excerpt
    assert excerpt.startswith("...(前文已省略)")
    assert excerpt.endswith("...(后文已截断)")
