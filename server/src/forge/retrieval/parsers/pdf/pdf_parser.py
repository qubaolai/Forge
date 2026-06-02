"""PDF 文档解析器.

P0 阶段使用 PyPDF 按页提取文本, 每页作为一个 H2 伪标题, 便于父子
切分器按页分组. P1 阶段将升级到 Unstructured hi_res 以处理扫描版
和复杂版式.
"""

import logging
from pathlib import Path

from pypdf import PdfReader

from forge.core.types import Element, ElementType
from forge.core.types.element import ElementMetadata
from forge.retrieval.parsers.parser_base import BaseParser

logger = logging.getLogger(__name__)


class PdfParser(BaseParser):
    """PDF 文档解析器."""

    def parse(self, file_path: Path) -> list[Element]:
        """按页解析 PDF 文档.

        文档名作为 H1 标题, 每页作为 H2 标题, 方便下游按页进行
        父子切分. 提取失败的页会被跳过并记录警告.

        Args:
            file_path: .pdf 文件路径.

        Returns:
            Element 列表. 若 PDF 无法打开则返回空列表.
        """
        try:
            reader = PdfReader(str(file_path))
        except Exception as e:
            logger.error("PDF 打开失败 %s: %s", file_path, e)
            return []

        elements: list[Element] = [
            Element(type=ElementType.TITLE, content=file_path.stem, level=1),
        ]

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as e:
                logger.warning("第 %d 页提取失败: %s", page_num, e)
                continue
            if not text:
                continue

            elements.append(
                Element(
                    type=ElementType.TITLE,
                    content=f"第 {page_num} 页",
                    level=2,
                    metadata=ElementMetadata(page_number=page_num),
                )
            )
            elements.append(
                Element(
                    type=ElementType.TEXT,
                    content=text,
                    metadata=ElementMetadata(page_number=page_num),
                )
            )

        logger.info("PDF 解析 %s: %d 页, %d 元素", file_path.name, len(reader.pages), len(elements))
        return elements
