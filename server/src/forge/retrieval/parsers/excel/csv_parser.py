"""CSV/TSV parser for knowledge-base ingestion."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from forge.core.types import Element
from forge.retrieval.parsers.parser_base import BaseParser

from .common import elements_from_tabular_rows

logger = logging.getLogger(__name__)


class CsvParser(BaseParser):
    """Parse CSV and TSV files as row-structured tabular data."""

    SUPPORTED_EXTENSIONS = [".csv", ".tsv"]

    def parse(self, file_path: Path) -> list[Element]:
        text = _read_text(file_path)
        if not text.strip():
            return []

        ext = file_path.suffix.lower()
        reader = _make_reader(text, delimiter="\t" if ext == ".tsv" else None)
        rows = [(index, row) for index, row in enumerate(reader, start=1)]
        elements = elements_from_tabular_rows(
            source="tsv" if ext == ".tsv" else "csv",
            sheet_name=file_path.stem,
            rows=rows,
        )
        logger.info("表格文本解析 %s: %d 元素", file_path.name, len(elements))
        return elements


def _read_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    logger.warning("表格文本编码识别失败, 使用 UTF-8 ignore: %s", file_path)
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _make_reader(text: str, *, delimiter: str | None) -> csv.reader:
    if delimiter is not None:
        return csv.reader(io.StringIO(text), delimiter=delimiter)

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.reader(io.StringIO(text), delimiter=",")
    return csv.reader(io.StringIO(text), dialect)
