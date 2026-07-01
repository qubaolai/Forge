"""检索结果 excerpt 构造工具."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"^[\u4e00-\u9fff]+$")
_PREFIX_OMITTED = "...(前文已省略)"
_SUFFIX_TRUNCATED = "...(后文已截断)"
_PLAIN_TRUNCATED = "...(已截断)"
_STOP_TERMS = {
    "什么",
    "是什么",
    "如何",
    "怎么",
    "哪些",
    "是否",
    "有没有",
    "根据",
    "文档",
    "知识库",
    "内容",
    "说明",
    "请问",
}


def build_query_focused_excerpt(
    content: str,
    query: str = "",
    *,
    max_chars: int,
) -> str:
    """为检索结果生成围绕 query 命中点的 excerpt.

    语义:
        - 内容未超长: 原样返回。
        - 能在正文中找到 query 关键词: 截取覆盖命中点的窗口。
        - 找不到关键词: 退回前缀截断。
    """
    text = (content or "").strip()
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    match_index = _best_match_index(text, query)
    if match_index is None:
        return text[:max_chars].rstrip() + _PLAIN_TRUNCATED

    start = max(0, match_index - max_chars // 3)
    end = min(len(text), start + max_chars)
    if end == len(text):
        start = max(0, end - max_chars)

    snippet = text[start:end].strip()
    if start > 0:
        snippet = _PREFIX_OMITTED + snippet
    if end < len(text):
        snippet = snippet + _SUFFIX_TRUNCATED
    return snippet


def _best_match_index(content: str, query: str) -> int | None:
    lower_content = content.lower()
    best_index: int | None = None
    best_len = 0
    for term in _query_terms(query):
        index = lower_content.find(term)
        if index < 0:
            continue
        is_better_len = len(term) > best_len
        is_same_len_earlier = (
            len(term) == best_len and (best_index is None or index < best_index)
        )
        if is_better_len or is_same_len_earlier:
            best_index = index
            best_len = len(term)
    return best_index


def _query_terms(query: str) -> list[str]:
    """提取少量高价值查询词, 中文长串补充 ngram 以提升命中率."""
    raw = (query or "").strip().lower()
    if not raw:
        return []

    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip().lower()
        if len(term) < 2 or term in _STOP_TERMS or term in terms:
            return
        terms.append(term)

    for match in _TOKEN_RE.finditer(raw[:120]):
        token = match.group(0)
        if _CJK_RE.fullmatch(token):
            if len(token) <= 8:
                add(token)
            max_n = min(6, len(token))
            for n in range(max_n, 1, -1):
                for start in range(0, len(token) - n + 1):
                    add(token[start : start + n])
                    if len(terms) >= 80:
                        break
                if len(terms) >= 80:
                    break
        else:
            add(token)
        if len(terms) >= 80:
            break

    return sorted(terms, key=len, reverse=True)
