"""Parser 分发器.

按文件扩展名查找已注册的 parser, 让上层 (KbIngestService / CLI 脚本)
不用关心格式细节. 新增格式只需在 default_dispatcher() 里加一行注册.

扩展点:
    - 增加新 parser: 实现 .parse(file_path) -> list[Element], 在
      default_dispatcher() 里 register(parser, [".ext"]) 即可
    - parser 缺少底层 SDK 时不要阻塞 import; 在 default_dispatcher() 内部
      try/except, 让其余 parser 能正常工作 (例如未装 python-docx 时 word
      parser 自动跳过, 但 txt/md/pdf 仍可用)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from forge.core.types import Element

logger = logging.getLogger(__name__)


class ParserLike(Protocol):
    """Parser 协议: 任何提供 parse(Path) -> list[Element] 的对象都行.

    项目内的 parsers (PdfParser / MdParser / TxtParser / WordParser) 并不
    都继承同一基类, 用 Protocol 鸭子类型即可.
    """

    def parse(self, file_path: Path) -> list[Element]: ...


class ParserDispatcher:
    """按扩展名管理 parser 实例."""

    def __init__(self) -> None:
        self._parsers: dict[str, ParserLike] = {}

    def register(self, parser: ParserLike, extensions: list[str]) -> None:
        """注册 parser. extensions 形如 [".pdf"] (大小写不敏感)."""
        for ext in extensions:
            ext_norm = ext.lower()
            if not ext_norm.startswith("."):
                ext_norm = "." + ext_norm
            existing = self._parsers.get(ext_norm)
            if existing is not None and existing is not parser:
                logger.warning(
                    "Parser 扩展名冲突: %s 已被 %s 占用, 现被 %s 覆盖",
                    ext_norm,
                    type(existing).__name__,
                    type(parser).__name__,
                )
            self._parsers[ext_norm] = parser

    def get(self, file_path: Path) -> ParserLike | None:
        """按扩展名取 parser, 找不到返回 None."""
        return self._parsers.get(file_path.suffix.lower())

    def supported_extensions(self) -> list[str]:
        return sorted(self._parsers.keys())


# ----------------------------------------------------------------------
# 默认装配
# ----------------------------------------------------------------------
def _safe_register(disp: ParserDispatcher, builder, extensions: list[str]) -> None:
    """单个 parser 加载失败不影响其他: 缺 SDK / 缺扩展时跳过."""
    try:
        parser = builder()
        disp.register(parser, extensions)
    except ImportError as e:
        logger.debug("parser 跳过 (依赖缺失): %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("parser 注册失败: %s", e)


def default_dispatcher() -> ParserDispatcher:
    """构造默认 dispatcher: 注册项目内置的 4 个 parser.

    每个 parser 独立 try, 缺依赖时只跳过该 parser. 失败的扩展名后续上层
    可以提示用户安装对应 extras (poetry install -E rag).
    """
    disp = ParserDispatcher()

    def _txt():
        from .text.txt_parser import TxtParser

        return TxtParser()

    def _md():
        from .text.md_parser import MdParser

        return MdParser()

    def _pdf():
        from .pdf.pdf_parser import PdfParser

        return PdfParser()

    def _word():
        from .word.word_parser import WordParser

        return WordParser()

    _safe_register(disp, _txt, [".txt"])
    _safe_register(disp, _md, [".md", ".markdown"])
    _safe_register(disp, _pdf, [".pdf"])
    _safe_register(disp, _word, [".docx"])

    return disp
