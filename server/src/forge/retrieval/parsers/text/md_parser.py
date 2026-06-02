"""Markdown 文档解析器.

自行解析 # 级别标题、代码块、表格, 精确保留层级结构.
不使用 UnstructuredMarkdownLoader (后者会丢失标题层级).
"""

import logging
import re
from pathlib import Path

from forge.core.types import Element, ElementType
from forge.retrieval.parsers.parser_base import BaseParser

logger = logging.getLogger(__name__)

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CODE_FENCE_PATTERN = re.compile(r"^```")
TABLE_SEP_PATTERN = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


class MdParser(BaseParser):
    """Markdown 文档解析器."""

    def parse(self, file_path: Path) -> list[Element]:
        """解析 Markdown 文档.

        支持以下结构:
            - ATX 风格标题 (# 到 ######);
            - 围栏代码块 (```);
            - 管道风格表格 (| col1 | col2 |).

        Args:
            file_path: .md 文件路径.

        Returns:
            Element 列表, 保留原文档的层级结构.
        """
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        elements: list[Element] = []
        buffer: list[str] = []
        in_code_block = False
        code_buffer: list[str] = []
        i = 0

        def flush_text():
            if buffer:
                content = "\n".join(buffer).strip()
                if content:
                    elements.append(Element(type=ElementType.TEXT, content=content))
                buffer.clear()

        while i < len(lines):
            line = lines[i]

            if CODE_FENCE_PATTERN.match(line):
                if in_code_block:
                    elements.append(
                        Element(
                            type=ElementType.CODE,
                            content="\n".join(code_buffer),
                        )
                    )
                    code_buffer.clear()
                    in_code_block = False
                else:
                    flush_text()
                    in_code_block = True
                i += 1
                continue

            if in_code_block:
                code_buffer.append(line)
                i += 1
                continue

            m = HEADING_PATTERN.match(line)
            if m:
                flush_text()
                level = len(m.group(1))
                elements.append(
                    Element(
                        type=ElementType.TITLE,
                        content=m.group(2),
                        level=level,
                    )
                )
                i += 1
                continue

            if "|" in line and i + 1 < len(lines) and TABLE_SEP_PATTERN.match(lines[i + 1]):
                flush_text()
                table_lines = [line, lines[i + 1]]
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    table_lines.append(lines[i])
                    i += 1
                elements.append(
                    Element(
                        type=ElementType.TABLE,
                        content="\n".join(table_lines),
                    )
                )
                continue

            buffer.append(line)
            i += 1

        flush_text()
        if code_buffer:
            elements.append(Element(type=ElementType.CODE, content="\n".join(code_buffer)))

        logger.info("MD 解析 %s: %d 元素", file_path.name, len(elements))
        return elements
