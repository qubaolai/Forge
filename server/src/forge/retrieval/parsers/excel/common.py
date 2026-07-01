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
    table_title: str | None = None
    header_detected = len(cleaned) > 1

    if len(cleaned) == 1:
        header_row_index = None
        columns = _make_generated_headers(max_cols)
        data_rows = cleaned
    elif _looks_like_title_row(cleaned[0][1], cleaned[1][1]):
        table_title = cleaned[0][1][0]
        header_row_index, header_cells = cleaned[1]
        columns = _make_headers(header_cells, max_cols)
        data_rows = cleaned[2:]
    else:
        header_row_index, header_cells = cleaned[0]
        columns = _make_headers(header_cells, max_cols)
        data_rows = cleaned[1:]

    sheet_payload = {
        "source": source,
        "sheet_name": sheet_name,
        "columns": columns,
        "header_row_index": header_row_index,
        "header_detected": header_detected,
        "table_title": table_title,
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


def _looks_like_title_row(first_row: list[str], second_row: list[str]) -> bool:
    first_count = _non_empty_count(first_row)
    second_count = _non_empty_count(second_row)
    return first_count == 1 and second_count > 1


def _non_empty_count(cells: Sequence[str]) -> int:
    return sum(1 for cell in cells if cell)


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
