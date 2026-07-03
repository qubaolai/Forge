"""把 parser 输出的 Element 列表渲染成"给人/LLM 阅读"的 Markdown.

与 chunker 的区别: chunker 面向检索 (父子块/行组/回灌预算), 这里面向阅读——
忠实还原整篇文档. 供 read_document 工具在对话里读取 Word/Excel/PDF 用.

Excel 按工作表 (sheet) 组织: 每个 sheet 一段 "## 工作表: X" + 前置元数据
(preamble) + markdown 表格.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from forge.core.types import Element, ElementType


@dataclass
class RenderedDocument:
    markdown: str
    sheet_names: list[str] = field(default_factory=list)  # 空 = 非表格文档


def elements_to_markdown(
    elements: list[Element],
    *,
    sheet_filter: str | None = None,
) -> RenderedDocument:
    """Element 列表 → Markdown. Excel 按 sheet 组织, sheet_filter 只取某页."""
    is_excel = any(el.type in (ElementType.SHEET_META, ElementType.ROW) for el in elements)
    if is_excel:
        return _render_excel(elements, sheet_filter=sheet_filter)
    return RenderedDocument(markdown=_render_generic(elements))


def _render_generic(elements: list[Element]) -> str:
    parts: list[str] = []
    for el in elements:
        if not el.content or el.type == ElementType.IMAGE:
            continue
        if el.type == ElementType.TITLE:
            level = min(el.level or 2, 6)
            parts.append(f"{'#' * level} {el.content}")
        elif el.type == ElementType.LIST:
            parts.append(f"- {el.content}")
        elif el.type == ElementType.CODE:
            parts.append(f"```\n{el.content}\n```")
        else:  # TEXT / TABLE (表格 content 已是 markdown)
            parts.append(el.content)
    return "\n\n".join(parts).strip()


def _render_excel(elements: list[Element], *, sheet_filter: str | None) -> RenderedDocument:
    sheets: list[dict] = []
    current: dict | None = None
    for el in elements:
        if el.type == ElementType.SHEET_META:
            meta = _load(el.content)
            name = str(meta.get("sheet_name") or f"工作表 {len(sheets) + 1}")
            current = {"name": name, "meta": meta, "rows": []}
            sheets.append(current)
        elif el.type == ElementType.ROW and current is not None:
            current["rows"].append(_load(el.content))

    names = [s["name"] for s in sheets]
    selected = sheets
    if sheet_filter:
        matched = [s for s in sheets if s["name"] == sheet_filter]
        selected = matched or sheets

    blocks: list[str] = []
    for sheet in selected:
        meta = sheet["meta"]
        columns = [c for c in (meta.get("columns") or []) if isinstance(c, str)]
        preamble = [p for p in (meta.get("preamble") or []) if isinstance(p, str)]
        lines = [f"## 工作表: {sheet['name']}"]
        lines.extend(preamble)
        if sheet["rows"] or columns:
            lines.append("")
            lines.append(_markdown_table(columns, sheet["rows"]))
        blocks.append("\n".join(lines).strip())

    return RenderedDocument(markdown="\n\n".join(blocks).strip(), sheet_names=names)


def _markdown_table(columns: list[str], rows: list[dict]) -> str:
    cols = columns or ["值"]
    lines = [
        "| " + " | ".join(_cell(col) for col in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        values = row.get("values")
        cells = row.get("cells")
        if isinstance(values, dict):
            rendered = [_cell(values.get(col, "")) for col in cols]
        elif isinstance(cells, list):
            rendered = [_cell(cells[i] if i < len(cells) else "") for i in range(len(cols))]
        else:
            rendered = [""] * len(cols)
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _load(content: str) -> dict:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
