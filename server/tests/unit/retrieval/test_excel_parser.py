from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.core.types import ElementType
from forge.retrieval.parsers.dispatcher import default_dispatcher
from forge.retrieval.parsers.excel.csv_parser import CsvParser


def test_csv_parser_emits_sheet_meta_and_rows(tmp_path):
    file_path = tmp_path / "score.csv"
    file_path.write_text("姓名,分数\n张三,98\n李四,87\n", encoding="utf-8")

    elements = CsvParser().parse(file_path)

    assert [element.type for element in elements] == [
        ElementType.SHEET_META,
        ElementType.ROW,
        ElementType.ROW,
    ]
    sheet = json.loads(elements[0].content)
    first_row = json.loads(elements[1].content)
    assert sheet["sheet_name"] == "score"
    assert sheet["columns"] == ["姓名", "分数"]
    assert first_row["row_index"] == 2
    assert first_row["values"] == {"姓名": "张三", "分数": "98"}


def test_tsv_parser_keeps_original_row_numbers_after_empty_rows(tmp_path):
    file_path = tmp_path / "items.tsv"
    file_path.write_text("编号\t名称\n1\t苹果\n\n2\t香蕉\n", encoding="utf-8")

    rows = [
        json.loads(element.content)
        for element in CsvParser().parse(file_path)
        if element.type == ElementType.ROW
    ]

    assert [row["row_index"] for row in rows] == [2, 4]
    assert rows[1]["values"] == {"编号": "2", "名称": "香蕉"}


def test_single_non_empty_row_is_preserved_as_data(tmp_path):
    file_path = tmp_path / "single.csv"
    file_path.write_text("文件名称\n", encoding="utf-8")

    elements = CsvParser().parse(file_path)
    sheet = json.loads(elements[0].content)
    row = json.loads(elements[1].content)

    assert sheet["columns"] == ["列1"]
    assert sheet["header_detected"] is False
    assert sheet["data_row_count"] == 1
    assert row["row_index"] == 1
    assert row["values"] == {"列1": "文件名称"}


def test_title_row_is_kept_as_sheet_metadata(tmp_path):
    file_path = tmp_path / "loan.csv"
    file_path.write_text(
        "日初借据文件\n文件名称,金额\nloan.csv,100\n",
        encoding="utf-8",
    )

    elements = CsvParser().parse(file_path)
    sheet = json.loads(elements[0].content)
    row = json.loads(elements[1].content)

    assert sheet["table_title"] == "日初借据文件"
    assert sheet["columns"] == ["文件名称", "金额"]
    assert sheet["header_row_index"] == 2
    assert sheet["data_row_count"] == 1
    assert row["row_index"] == 3
    assert row["values"] == {"文件名称": "loan.csv", "金额": "100"}


def test_xlsx_parser_emits_rows_per_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    file_path = tmp_path / "sales.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "销售"
    sheet.append(["产品", "金额"])
    sheet.append(["A", 100])
    sheet.append([None, None])
    sheet.append(["B", 120])
    workbook.save(file_path)
    workbook.close()

    from forge.retrieval.parsers.excel.xlsx_parser import XlsxParser

    elements = XlsxParser().parse(file_path)
    rows = [
        json.loads(element.content)
        for element in elements
        if element.type == ElementType.ROW
    ]

    assert json.loads(elements[0].content)["sheet_name"] == "销售"
    assert [row["row_index"] for row in rows] == [2, 4]
    assert rows[0]["values"] == {"产品": "A", "金额": "100"}


def test_xlsx_parser_keeps_formula_text_when_cached_value_is_empty(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    file_path = tmp_path / "formula.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "日初借据文件"
    sheet.append(["文件名称"])
    sheet.append(['=CONCAT("loan", ".csv")'])
    workbook.save(file_path)
    workbook.close()

    from forge.retrieval.parsers.excel.xlsx_parser import XlsxParser

    elements = XlsxParser().parse(file_path)
    rows = [
        json.loads(element.content)
        for element in elements
        if element.type == ElementType.ROW
    ]

    assert len(rows) == 1
    assert rows[0]["row_index"] == 2
    assert rows[0]["values"] == {"文件名称": '=CONCAT("loan", ".csv")'}


def test_default_dispatcher_registers_tabular_parsers():
    dispatcher = default_dispatcher()

    assert dispatcher.get(Path("dummy.csv")) is not None
    assert dispatcher.get(Path("dummy.tsv")) is not None
    assert ".csv" in dispatcher.supported_extensions()
    assert ".tsv" in dispatcher.supported_extensions()


def test_default_dispatcher_registers_xlsx_when_openpyxl_is_available():
    pytest.importorskip("openpyxl")
    dispatcher = default_dispatcher()

    assert dispatcher.get(Path("dummy.xlsx")) is not None
    assert ".xlsx" in dispatcher.supported_extensions()
