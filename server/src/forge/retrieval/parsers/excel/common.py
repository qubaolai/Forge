"""Tabular parser helpers shared by Excel-like formats."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from forge.core.types import Element, ElementMetadata, ElementType


def normalize_cell(value: Any) -> str:
    """Convert a cell value into a stable, searchable string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def elements_from_tabular_rows(
    *,
    source: str,
    sheet_name: str,
    rows: Sequence[tuple[int, Sequence[Any]]],
) -> list[Element]:
    """Build SHEET_META and ROW elements from a 2D table.

    The first non-empty row is treated as the header row. Empty rows are
    ignored, but the original row numbers are kept for later chunk metadata.
    """
    cleaned = _clean_rows(rows)
    if not cleaned:
        return []

    max_cols = max(len(cells) for _, cells in cleaned)
    preamble: list[str] = []
    if len(cleaned) == 1:
        # 单行: 无表头, 生成占位列, 整行作为数据 (保留内容, 不丢)
        header_row_index = None
        columns = _make_generated_headers(max_cols)
        data_rows = cleaned
        header_detected = False
    else:
        # 真实表头 = 第一个"满列宽"行 (跳过前置元数据/说明行);
        # 表头之前的窄行作为 preamble 上下文 (文件名称/说明/目录 等).
        header_pos = _find_header_pos(cleaned, max_cols)
        preamble = _render_preamble([cells for _, cells in cleaned[:header_pos]])
        header_row_index, header_cells = cleaned[header_pos]
        columns = _make_headers(header_cells, max_cols)
        data_rows = cleaned[header_pos + 1 :]
        header_detected = bool(data_rows)

    sheet_payload = {
        "source": source,
        "sheet_name": sheet_name,
        "columns": columns,
        "header_row_index": header_row_index,
        "header_detected": header_detected,
        "preamble": preamble,
        "table_title": None,
        "data_row_count": len(data_rows),
    }
    elements = [
        Element(
            type=ElementType.SHEET_META,
            content=_dump_json(sheet_payload),
            metadata=ElementMetadata(
                original_label="Sheet",
                original_type=f"{source}_sheet",
                text_length=len(sheet_name),
            ),
        )
    ]

    for row_index, cells in data_rows:
        padded = _pad(cells, max_cols)
        values = {columns[i]: padded[i] for i in range(max_cols)}
        row_payload = {
            "source": source,
            "sheet_name": sheet_name,
            "row_index": row_index,
            "values": values,
            "cells": padded,
        }
        row_content = _dump_json(row_payload)
        elements.append(
            Element(
                type=ElementType.ROW,
                content=row_content,
                metadata=ElementMetadata(
                    original_label="Row",
                    original_type=f"{source}_row",
                    text_length=len(row_content),
                ),
            )
        )

    return elements


def _clean_rows(rows: Sequence[tuple[int, Sequence[Any]]]) -> list[tuple[int, list[str]]]:
    cleaned: list[tuple[int, list[str]]] = []
    for row_index, raw_cells in rows:
        cells = _trim_trailing_empty([normalize_cell(value) for value in raw_cells])
        if any(cells):
            cleaned.append((row_index, cells))
    return cleaned


def _make_headers(header_cells: list[str], max_cols: int) -> list[str]:
    headers = _pad(header_cells, max_cols)
    seen: dict[str, int] = {}
    result: list[str] = []
    for index, raw in enumerate(headers):
        base = raw or f"列{index + 1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def _make_generated_headers(max_cols: int) -> list[str]:
    return [f"列{index + 1}" for index in range(max_cols)]


def _find_header_pos(cleaned: list[tuple[int, list[str]]], max_cols: int) -> int:
    """定位真实表头行下标.

    表头 = 第一个列宽达到全表最大列宽、且其后仍有数据行的行. 之前的窄行
    (文件名称/文件说明/字段说明 等) 视为前置元数据. 找不到则退化为第 0 行.
    """
    for index, (_, cells) in enumerate(cleaned):
        if len(cells) == max_cols and index < len(cleaned) - 1:
            return index
    return 0


def _render_preamble(rows: list[list[str]]) -> list[str]:
    """把表头前的窄行渲染成上下文行: 两列→"键: 值", 其余→" | " 连接."""
    lines: list[str] = []
    for cells in rows:
        nonempty = [cell for cell in cells if cell]
        if not nonempty:
            continue
        if len(nonempty) == 2:
            lines.append(f"{nonempty[0]}: {nonempty[1]}")
        else:
            lines.append(" | ".join(nonempty))
    return lines


def _pad(cells: Sequence[str], size: int) -> list[str]:
    result = list(cells[:size])
    if len(result) < size:
        result.extend([""] * (size - len(result)))
    return result


def _trim_trailing_empty(cells: list[str]) -> list[str]:
    end = len(cells)
    while end > 0 and cells[end - 1] == "":
        end -= 1
    return cells[:end]


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
