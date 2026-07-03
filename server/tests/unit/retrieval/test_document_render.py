"""document_render: Element → Markdown 渲染 (read_document 用)."""

from __future__ import annotations

import json

from forge.core.types import Element, ElementType
from forge.retrieval.document_render import elements_to_markdown


def _sheet_meta(name: str, columns: list[str], preamble: list[str]) -> Element:
    payload = {"sheet_name": name, "columns": columns, "preamble": preamble}
    return Element(type=ElementType.SHEET_META, content=json.dumps(payload, ensure_ascii=False))


def _row(name: str, values: dict) -> Element:
    payload = {"sheet_name": name, "values": values, "cells": list(values.values())}
    return Element(type=ElementType.ROW, content=json.dumps(payload, ensure_ascii=False))


def test_generic_document_renders_titles_text_and_table():
    elements = [
        Element(type=ElementType.TITLE, content="订单说明", level=2),
        Element(type=ElementType.TEXT, content="这是正文段落。"),
        Element(type=ElementType.TABLE, content="| 字段 | 说明 |\n| --- | --- |\n| id | 主键 |"),
    ]

    out = elements_to_markdown(elements)

    assert out.sheet_names == []
    assert "## 订单说明" in out.markdown
    assert "这是正文段落。" in out.markdown
    assert "| 字段 | 说明 |" in out.markdown


def test_excel_rendered_by_sheet_with_preamble():
    elements = [
        _sheet_meta("放款借据明细", ["字段", "名称"], ["文件名称: 放款借据明细", "文件说明: xxx"]),
        _row("放款借据明细", {"字段": "cur_date", "名称": "账务日期"}),
        _row("放款借据明细", {"字段": "loan_id", "名称": "借据号"}),
        _sheet_meta("利息费率", ["费率", "生效日"], []),
        _row("利息费率", {"费率": "0.05", "生效日": "20200101"}),
    ]

    out = elements_to_markdown(elements)

    assert out.sheet_names == ["放款借据明细", "利息费率"]
    # 按 sheet 分段 + preamble 上下文 + markdown 表格
    assert "## 工作表: 放款借据明细" in out.markdown
    assert "文件名称: 放款借据明细" in out.markdown
    assert "| 字段 | 名称 |" in out.markdown
    assert "| cur_date | 账务日期 |" in out.markdown
    assert "## 工作表: 利息费率" in out.markdown


def test_excel_sheet_filter_returns_only_selected_sheet():
    elements = [
        _sheet_meta("A", ["c"], []),
        _row("A", {"c": "a1"}),
        _sheet_meta("B", ["c"], []),
        _row("B", {"c": "b1"}),
    ]

    out = elements_to_markdown(elements, sheet_filter="B")

    assert out.sheet_names == ["A", "B"]  # 列表仍给全量, 便于 LLM 知道有哪些页
    assert "## 工作表: B" in out.markdown
    assert "b1" in out.markdown
    assert "## 工作表: A" not in out.markdown


def test_excel_end_to_end_parse_and_render_with_metadata_preamble(tmp_path):
    # 用真实 CSV parser 走一遍 (对账文件典型结构: 前置元数据 + 表头在下)
    from forge.retrieval.parsers.excel.csv_parser import CsvParser

    csv_path = tmp_path / "spec.csv"
    csv_path.write_text(
        "文件名称,放款借据明细\n字段说明\n字段,名称,类型\ncur_date,账务日期,string\n",
        encoding="utf-8",
    )
    elements = CsvParser().parse(csv_path)

    out = elements_to_markdown(elements)

    assert "文件名称: 放款借据明细" in out.markdown
    assert "字段说明" in out.markdown
    assert "| 字段 | 名称 | 类型 |" in out.markdown
    assert "| cur_date | 账务日期 | string |" in out.markdown
