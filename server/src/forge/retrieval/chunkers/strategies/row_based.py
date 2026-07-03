"""Row-based chunker for Excel-like tabular documents."""

from __future__ import annotations

import json
import logging
from typing import Any

from forge.core.types import Chunk, ChunkMetadata, ChunkStrategy, Element, ElementType
from forge.retrieval.chunkers.base import BaseChunker

logger = logging.getLogger(__name__)

# 切分阈值/粒度参数已收敛进 ChunkConfig (excel_* 字段), 此处只保留固定标签集
_EXCEL_PARENT_SPLITTERS = {"excel_sheet", "excel_row_groups", "excel_rows"}


class RowBasedChunker(BaseChunker):
    """Chunk Excel-like sheets as sheet/large-row-group parents plus row-group children."""

    def _build_parents(self, elements: list[Element]) -> list[dict]:
        sheets = self._collect_sheets(elements)
        parents: list[dict] = []
        for sheet_index, sheet in enumerate(sheets):
            columns = self._columns_for_sheet(sheet)
            rows = sheet["rows"]
            if not rows:
                parent = self._build_empty_sheet_parent(sheet, columns, sheet_index)
                if parent is not None:
                    parents.append(parent)
                continue
            parents.extend(self._pack_rows(sheet, columns, sheet_index))
        return parents

    def _build_children(
        self,
        parent: Chunk,
        doc_id: str,
        doc_version: str,
        elements: list[Element] | None = None,
    ) -> list[Chunk]:
        """Excel 子块: 整表优先 (保表结构定义语义), 超预算才按完整行组切.

        父块内容 (工作表名/表标题/行范围 + markdown 整表) 已自带上下文;
        子块直接复用之, 小表整块作单个子块 (一表一向量, 适合表结构定义/数据字典),
        大表按 table_child_max_chars 拆完整行组. 子块相对行号经 row_indices
        映射回真实行号, 供 P1-3 回灌定位命中行.
        """
        extra = parent.metadata.extra if parent.metadata is not None else {}
        if (
            parent.source_type != "table"
            or extra.get("parent_splitter") not in _EXCEL_PARENT_SPLITTERS
        ):
            return super()._build_children(parent, doc_id, doc_version, elements)

        base_extra = {
            "table_index": extra.get("table_index"),
            "sheet_name": extra.get("sheet_name"),
        }
        slices = self._split_table_content(
            parent.content,
            base_extra=base_extra,
            target_chars=self.config.table_child_max_chars,
            splitter_label="excel_table",
        )
        row_indices = extra.get("row_indices")
        children: list[Chunk] = []
        for idx, child_slice in enumerate(s for s in slices if s.content.strip()):
            child_extra = dict(child_slice.extra or {})
            if isinstance(row_indices, list):
                self._map_child_row_range(child_extra, row_indices)
            children.append(
                self._make_child(
                    parent,
                    doc_id,
                    doc_version,
                    idx,
                    child_slice.content,
                    source_type=child_slice.source_type,
                    extra=child_extra,
                )
            )
        return children

    def _collect_sheets(self, elements: list[Element]) -> list[dict]:
        sheets: list[dict] = []
        by_name: dict[str, dict] = {}
        current: dict | None = None

        def get_sheet(name: str, payload: dict[str, Any] | None = None) -> dict:
            nonlocal current
            sheet = by_name.get(name)
            if sheet is None:
                payload = payload or {}
                sheet = {
                    "sheet_name": name,
                    "source": payload.get("source") or "tabular",
                    "columns": self._string_list(payload.get("columns")),
                    "header_row_index": self._to_int(payload.get("header_row_index")),
                    "header_detected": bool(payload.get("header_detected")),
                    "table_title": self._safe_string(payload.get("table_title")) or None,
                    "preamble": self._string_list(payload.get("preamble")),
                    "data_row_count": self._to_int(payload.get("data_row_count")),
                    "rows": [],
                }
                by_name[name] = sheet
                sheets.append(sheet)
            elif payload:
                columns = self._string_list(payload.get("columns"))
                if columns:
                    sheet["columns"] = columns
                sheet["source"] = payload.get("source") or sheet["source"]
                sheet["header_row_index"] = self._to_int(
                    payload.get("header_row_index")
                ) or sheet.get("header_row_index")
                sheet["header_detected"] = bool(payload.get("header_detected"))
                sheet["table_title"] = self._safe_string(payload.get("table_title")) or None
                preamble = self._string_list(payload.get("preamble"))
                if preamble:
                    sheet["preamble"] = preamble
                sheet["data_row_count"] = self._to_int(payload.get("data_row_count")) or sheet.get(
                    "data_row_count"
                )
            current = sheet
            return sheet

        for element in elements:
            if element.type == ElementType.SHEET_META:
                payload = self._load_payload(element.content)
                sheet_name = self._safe_string(payload.get("sheet_name")) or (
                    f"工作表 {len(sheets) + 1}"
                )
                get_sheet(sheet_name, payload)
                continue

            if element.type != ElementType.ROW:
                continue

            payload = self._load_payload(element.content)
            sheet_name = self._safe_string(payload.get("sheet_name"))
            if not sheet_name and current is not None:
                sheet_name = current["sheet_name"]
            sheet = get_sheet(sheet_name or f"工作表 {len(sheets) + 1}")
            sheet["rows"].append(payload)

        return sheets

    def _pack_rows(self, sheet: dict, columns: list[str], sheet_index: int) -> list[dict]:
        cfg = self.config
        rows = sheet["rows"]
        full_content = self._render_table(sheet, columns, rows)
        row_count = len(rows)
        if (
            row_count <= cfg.excel_sheet_parent_max_rows
            and len(full_content) <= cfg.excel_sheet_parent_max_chars
        ):
            mode = "small_sheet" if row_count <= cfg.excel_small_sheet_max_rows else "medium_sheet"
            return [
                self._make_row_parent(
                    sheet,
                    columns,
                    rows,
                    sheet_index,
                    parent_splitter="excel_sheet",
                    chunking_mode=mode,
                )
            ]

        parents: list[dict] = []
        buffer: list[dict] = []

        def flush() -> None:
            nonlocal buffer
            if not buffer:
                return
            parents.append(
                self._make_row_parent(
                    sheet,
                    columns,
                    buffer,
                    sheet_index,
                    parent_splitter="excel_row_groups",
                    chunking_mode="large_sheet_row_group",
                )
            )
            buffer = []

        for row in rows:
            candidate_rows = [*buffer, row]
            candidate = self._render_table(sheet, columns, candidate_rows)
            if buffer and (
                len(buffer) >= cfg.excel_large_parent_rows
                or len(candidate) > cfg.excel_large_parent_max_chars
            ):
                flush()
                buffer = [row]
            else:
                buffer.append(row)

        flush()
        return parents

    def _make_row_parent(
        self,
        sheet: dict,
        columns: list[str],
        rows: list[dict],
        sheet_index: int,
        *,
        parent_splitter: str,
        chunking_mode: str,
    ) -> dict:
        row_indices = [self._row_index(row, fallback=index + 1) for index, row in enumerate(rows)]
        row_start = row_indices[0]
        row_end = row_indices[-1]
        sheet_name = sheet["sheet_name"]
        row_label = f"行 {row_start}" if row_start == row_end else f"行 {row_start}-{row_end}"
        return {
            "content": self._render_table(sheet, columns, rows),
            "header_path": f"工作表 {sheet_name} > {row_label}",
            "source_type": "table",
            "metadata": ChunkMetadata(
                strategy=ChunkStrategy.ROW_BASED,
                element_count=len(rows),
                has_table=True,
                extra={
                    "sheet_name": sheet_name,
                    "source": sheet.get("source") or "tabular",
                    "header_row_index": sheet.get("header_row_index"),
                    "header_detected": sheet.get("header_detected"),
                    "table_title": sheet.get("table_title"),
                    "columns": columns,
                    "row_start": row_start,
                    "row_end": row_end,
                    "row_indices": row_indices,
                    "row_count": len(rows),
                    "sheet_row_count": sheet.get("data_row_count") or len(sheet["rows"]),
                    "table_index": sheet_index,
                    "parent_splitter": parent_splitter,
                    "chunking_mode": chunking_mode,
                },
            ),
        }

    def _build_empty_sheet_parent(
        self,
        sheet: dict,
        columns: list[str],
        sheet_index: int,
    ) -> dict | None:
        preamble = self._string_list(sheet.get("preamble"))
        if not columns and not preamble:
            return None
        sheet_name = sheet["sheet_name"]
        content = "\n".join(
            [
                f"工作表: {sheet_name}",
                *preamble,
                f"列: {', '.join(columns)}" if columns else "",
                "数据行: 0",
            ]
        )
        return {
            "content": content,
            "header_path": f"工作表 {sheet_name}",
            "source_type": "text",
            "metadata": ChunkMetadata(
                strategy=ChunkStrategy.ROW_BASED,
                element_count=0,
                has_table=False,
                extra={
                    "sheet_name": sheet_name,
                    "source": sheet.get("source") or "tabular",
                    "header_row_index": sheet.get("header_row_index"),
                    "header_detected": sheet.get("header_detected"),
                    "table_title": sheet.get("table_title"),
                    "columns": columns,
                    "row_count": 0,
                    "sheet_row_count": sheet.get("data_row_count") or 0,
                    "table_index": sheet_index,
                    "parent_splitter": "excel_sheet",
                    "chunking_mode": "empty_sheet",
                },
            ),
        }

    @classmethod
    def _columns_for_sheet(cls, sheet: dict) -> list[str]:
        columns = cls._string_list(sheet.get("columns"))
        for row in sheet["rows"]:
            values = row.get("values")
            if isinstance(values, dict):
                for key in values:
                    text = cls._safe_string(key)
                    if text and text not in columns:
                        columns.append(text)
            cells = row.get("cells")
            if isinstance(cells, list):
                while len(columns) < len(cells):
                    columns.append(f"列{len(columns) + 1}")
        return columns

    def _render_table(self, sheet: dict, columns: list[str], rows: list[dict]) -> str:
        row_indices = [self._row_index(row, fallback=index + 1) for index, row in enumerate(rows)]
        row_start = row_indices[0]
        row_end = row_indices[-1]
        sheet_name = sheet["sheet_name"]
        parts = [
            f"工作表: {sheet_name}",
        ]
        # 前置元数据 (文件名称/说明/目录 等) 作为整表上下文
        parts.extend(self._string_list(sheet.get("preamble")))
        if sheet.get("table_title"):
            parts.append(f"表标题: {sheet['table_title']}")
        parts.extend(
            [
                f"行范围: {row_start}-{row_end}" if row_start != row_end else f"行范围: {row_start}",
                "",
                self._markdown_table(columns, rows),
            ]
        )
        return "\n".join(parts).strip()

    @classmethod
    def _markdown_table(cls, columns: list[str], rows: list[dict]) -> str:
        safe_columns = columns or ["值"]
        lines = [
            "| " + " | ".join(cls._markdown_cell(col) for col in safe_columns) + " |",
            "| " + " | ".join("---" for _ in safe_columns) + " |",
        ]
        for row in rows:
            values = row.get("values")
            cells = row.get("cells")
            if isinstance(values, dict):
                rendered = [cls._markdown_cell(values.get(col, "")) for col in safe_columns]
            elif isinstance(cells, list):
                rendered = [
                    cls._markdown_cell(cells[index] if index < len(cells) else "")
                    for index in range(len(safe_columns))
                ]
            else:
                rendered = [""] * len(safe_columns)
            lines.append("| " + " | ".join(rendered) + " |")
        return "\n".join(lines)

    @staticmethod
    def _markdown_cell(value: Any) -> str:
        text = "" if value is None else str(value)
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()

    @classmethod
    def _map_child_row_range(cls, extra: dict, row_indices: list[Any]) -> None:
        """把子块的相对行号 (1-based) 映射回父块记录的真实行号, 供回灌定位命中行."""
        rel_start = cls._to_int(extra.get("row_start"))
        rel_end = cls._to_int(extra.get("row_end"))
        if rel_start is None or rel_end is None:
            return
        clean_indices = [value for value in (cls._to_int(v) for v in row_indices) if value is not None]
        if not clean_indices:
            return
        start_idx = min(max(rel_start - 1, 0), len(clean_indices) - 1)
        end_idx = min(max(rel_end - 1, start_idx), len(clean_indices) - 1)
        mapped = clean_indices[start_idx : end_idx + 1]
        if not mapped:
            return
        extra["row_start"] = mapped[0]
        extra["row_end"] = mapped[-1]
        extra["row_indices"] = mapped

    @staticmethod
    def _row_index(row: dict, *, fallback: int) -> int:
        return RowBasedChunker._to_int(row.get("row_index")) or fallback

    @staticmethod
    def _load_payload(content: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            logger.warning("ROW/SHEET_META 内容不是合法 JSON, 已忽略: %r", content[:120])
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _safe_string(value: Any) -> str:
        return "" if value is None else str(value).strip()

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [text for item in value if (text := RowBasedChunker._safe_string(item))]

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None
