"""TXT 文档解析器.

TXT 没有结构信息, 整个文件作为单个文本元素输出. 文件名作为 H1
伪标题, 便于下游 chunker 生成 header_path.
"""

import logging
from pathlib import Path

from forge.core.types import Element, ElementType
from forge.retrieval.parsers.parser_base import BaseParser

logger = logging.getLogger(__name__)


class TxtParser(BaseParser):
    """TXT 文档解析器."""

    def parse(self, file_path: Path) -> list[Element]:
        """解析 TXT 文档.

        优先使用 UTF-8 编码, 失败时降级到 GBK (中文 Windows 常见).
        空文件返回空列表.

        Args:
            file_path: .txt 文件路径.

        Returns:
            Element 列表: 含一个文件名 H1 标题和一个正文元素.
        """
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("UTF-8 解码失败, 尝试 GBK: %s", file_path)
            text = file_path.read_text(encoding="gbk", errors="ignore")

        text = text.strip()
        if not text:
            return []

        elements = [
            Element(type=ElementType.TITLE, content=file_path.stem, level=1),
            Element(type=ElementType.TEXT, content=text),
        ]
        logger.info("TXT 解析 %s: %d 字符", file_path.name, len(text))
        return elements
