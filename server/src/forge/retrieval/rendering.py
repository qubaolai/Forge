"""父块回灌渲染: 把检索到的父块按 token 预算渲染成给 LLM 的片段.

设计目标 (对应优化方案 P1):
    - 兑现 small-to-big: 父块装得下预算就整块回灌 (文本/表格一致).
    - 不爆上下文: 超预算时降级——
        * 文本/mixed: 以 query 命中点为锚向两端扩展到预算 (复用 excerpt).
        * 表格 (含整张 Excel sheet): 超预算退化为"表头 + 命中行组",
          并注明"全表共 Y 行", 绝不按字符切断产生残缺 markdown.
    - 预算用真实 token 计量 (token_meter), 而非字符数.

命中行定位优先级 (表格):
    1. hit_chunk_ids → child_debug_manifest 的 row_start/row_end → 命中原始行,
       再经父块 row_indices 映射到 markdown 数据行位置 (Excel 精确路径).
    2. 退化: 数据行中包含 query 关键词的行 (Word/MD 表格等无行号映射时).
    3. 再退化: 全部数据行 (由预算裁剪), 保证有输出.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge.retrieval.chunkers.base import BaseChunker
from forge.retrieval.common.excerpt import _query_terms, build_query_focused_excerpt

if TYPE_CHECKING:
    from forge.retrieval.base import RetrievedParent

# 估算"1 token ≈ 多少字符"的起始系数, 仅用于给 excerpt 一个初始字符预算,
# 之后用真实 token 计量迭代收敛, 因此系数不需要精确.
_CHARS_PER_TOKEN = 3.0


def count_tokens(text: str) -> int:
    """真实 token 计量, token_meter 不可用时退化为字符数 // 4."""
    if not text:
        return 0
    try:
        from forge.context_mgmt.meter.token_meter import get_token_meter

        return int(get_token_meter().count_text(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def render_parent_snippet(
    parent: RetrievedParent,
    query: str,
    *,
    budget_tokens: int,
) -> str:
    """把一个检索到的父块渲染成不超过 budget_tokens 的回灌片段."""
    content = (parent.content or "").strip()
    if not content or budget_tokens <= 0:
        return ""
    if count_tokens(content) <= budget_tokens:
        return content  # 整块回灌 (兑现 small-to-big)
    if (parent.source_type or "") == "table":
        return _render_table_hit_rows(parent, query, budget_tokens)
    return _render_text_anchored(content, query, budget_tokens)


# ----------------------------------------------------------------------
# 文本: 命中锚点 + 向两端扩展到预算
# ----------------------------------------------------------------------
def _render_text_anchored(content: str, query: str, budget_tokens: int) -> str:
    """超预算文本片段: 复用 query-focused excerpt, 用真实 token 迭代收敛到预算."""
    approx_chars = max(1, int(budget_tokens * _CHARS_PER_TOKEN))
    snippet = content
    for _ in range(4):
        snippet = build_query_focused_excerpt(content, query, max_chars=approx_chars)
        toks = count_tokens(snippet)
        if toks <= budget_tokens:
            return snippet
        approx_chars = max(1, int(approx_chars * budget_tokens / max(toks, 1) * 0.9))
    return snippet


# ----------------------------------------------------------------------
# 表格: 整表优先 (已在上层判断), 超预算退命中行组
# ----------------------------------------------------------------------
def _render_table_hit_rows(
    parent: RetrievedParent,
    query: str,
    budget_tokens: int,
) -> str:
    content = (parent.content or "").strip()
    parsed = _parse_markdown_table(content)
    if parsed is None:
        # 不是可识别的 markdown 表格, 退回文本锚窗
        return _render_text_anchored(content, query, budget_tokens)

    prefix_lines, header_lines, data_rows, _suffix = parsed
    if not data_rows:
        return _render_text_anchored(content, query, budget_tokens)

    positions = _select_hit_positions(parent, data_rows, query)
    total_rows = _total_row_count(parent, data_rows)

    # 在预算内尽量多放命中行 (保持文档顺序)
    chosen: list[str] = []
    for pos in positions:
        candidate = _join_table(prefix_lines, header_lines, [*chosen, data_rows[pos]], note=None)
        if chosen and count_tokens(candidate) > budget_tokens:
            break
        chosen.append(data_rows[pos])

    if not chosen and positions:
        chosen = [data_rows[positions[0]]]  # 至少给一行, 哪怕略超

    note = None
    if len(chosen) < total_rows:
        note = f"（仅显示命中的 {len(chosen)} 行，全表共 {total_rows} 行）"
    return _join_table(prefix_lines, header_lines, chosen, note=note)


def _select_hit_positions(
    parent: RetrievedParent,
    data_rows: list[str],
    query: str,
) -> list[int]:
    """定位命中的数据行位置 (0-based, 相对 data_rows)."""
    meta: dict[str, Any] = parent.metadata or {}
    row_indices = meta.get("row_indices")
    manifest = meta.get("child_debug_manifest") or []
    hit_ids = set(parent.hit_chunk_ids or [])

    ranges: list[tuple[int, int]] = []
    for cd in manifest:
        if not isinstance(cd, dict) or cd.get("id") not in hit_ids:
            continue
        lo = _to_int(cd.get("row_start"))
        hi = _to_int(cd.get("row_end"))
        if lo is not None and hi is not None:
            ranges.append((lo, hi))

    positions: list[int] = []
    # 1. 精确: row_indices 对齐 data_rows + 命中行区间
    if isinstance(row_indices, list) and len(row_indices) == len(data_rows) and ranges:
        for i, ri in enumerate(row_indices):
            rii = _to_int(ri)
            if rii is not None and any(lo <= rii <= hi for lo, hi in ranges):
                positions.append(i)

    # 2. 退化: 关键词命中行
    if not positions:
        terms = _query_terms(query)
        if terms:
            for i, row in enumerate(data_rows):
                low = row.lower()
                if any(term in low for term in terms):
                    positions.append(i)

    # 3. 再退化: 全部行 (由预算裁剪)
    if not positions:
        positions = list(range(len(data_rows)))
    return positions


def _total_row_count(parent: RetrievedParent, data_rows: list[str]) -> int:
    """全表行数: 优先父块元数据里的 sheet_row_count (大表行组场景更准)."""
    meta: dict[str, Any] = parent.metadata or {}
    sheet_rows = _to_int(meta.get("sheet_row_count"))
    if sheet_rows is not None and sheet_rows >= len(data_rows):
        return sheet_rows
    return len(data_rows)


# ----------------------------------------------------------------------
# markdown 表格解析 / 拼装 (复用 BaseChunker 的表格语法判定, 避免重复)
# ----------------------------------------------------------------------
def _parse_markdown_table(
    content: str,
) -> tuple[list[str], list[str], list[str], list[str]] | None:
    """把父块内容拆成 (前置上下文行, 表头行, 数据行, 后缀行). 无表格返回 None."""
    lines = content.splitlines()
    start = BaseChunker._find_table_start(lines)
    if start is None:
        return None
    end = start
    while end < len(lines) and BaseChunker._is_table_line(lines[end]):
        end += 1
    table_lines = lines[start:end]
    header_len = (
        2 if len(table_lines) >= 2 and BaseChunker._is_table_separator(table_lines[1]) else 1
    )
    header_lines = table_lines[:header_len]
    data_rows = table_lines[header_len:]
    prefix_lines = [line for line in lines[:start] if line.strip()]
    suffix_lines = [line for line in lines[end:] if line.strip()]
    return prefix_lines, header_lines, data_rows, suffix_lines


def _join_table(
    prefix_lines: list[str],
    header_lines: list[str],
    rows: list[str],
    *,
    note: str | None,
) -> str:
    parts: list[str] = []
    if prefix_lines:
        parts.extend(prefix_lines)
        parts.append("")
    parts.extend(header_lines)
    parts.extend(rows)
    if note:
        parts.append("")
        parts.append(note)
    return "\n".join(parts).strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
