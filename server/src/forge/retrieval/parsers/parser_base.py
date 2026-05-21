"""Parser 抽象基类.

所有格式的 parser 都实现这个接口, 输入文件路径, 输出 Element 列表.
不做切分, 不计算 hash, 不关心入库流程.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from forge.core.types import Element


class BaseParser(ABC):
    """文档解析器基类."""

    #: 该 parser 支持的扩展名列表 (含点, 小写), 子类必须覆盖.
    SUPPORTED_EXTENSIONS: list[str] = []

    @abstractmethod
    def parse(self, file_path: Path) -> list[Element]:
        """解析文件为 Element 列表.

        Raises:
            FileNotFoundError: 文件不存在.
            ValueError: 格式不支持或文件损坏.
        """
        ...

    def supports(self, file_path: Path) -> bool:
        """判断当前 parser 是否能处理此文件."""
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS
