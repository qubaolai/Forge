"""检索查询归一化 (retrieval/common/query.py) 单测."""

from __future__ import annotations

import pytest

from forge.retrieval.common.query import normalize_query


@pytest.mark.parametrize("value", ["", "   ", "\n\t "])
def test_empty_or_blank_becomes_empty(value: str) -> None:
    assert normalize_query(value) == ""


def test_collapses_and_strips_whitespace() -> None:
    assert normalize_query("  苹果   的   功效  ") == "苹果 的 功效"


def test_fullwidth_ascii_folds_to_halfwidth() -> None:
    # NFKC: 全角字母数字 → 半角, 便于与语料里的半角 API/123 匹配
    assert normalize_query("ＡＰＩ１２３") == "API123"


def test_plain_simplified_query_is_stable_and_idempotent() -> None:
    once = normalize_query("知识库检索精度")
    assert once == "知识库检索精度"
    assert normalize_query(once) == once


def test_does_not_crash_on_traditional_input() -> None:
    # 繁→简 依赖可选库; 无库时应原样返回而非报错
    result = normalize_query("知識庫檢索")
    assert isinstance(result, str) and result
