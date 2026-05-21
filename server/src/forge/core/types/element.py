"""文档元素数据模型.

定义所有格式 parser 的统一输出结构. Word/PDF/Excel/TXT/MD 的解析器
都应该输出 Element 列表, 下游 chunker 只认 Element, 不关心原格式.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ElementType(str, Enum):
    """文档元素类型枚举.

    覆盖 Word/PDF/Excel 等所有格式可能产生的元素类型. 其中 ROW 和
    SHEET_META 专供 Excel 使用, 其他格式按需使用通用类型.
    """

    TITLE = "title"  # Word 的标题 / Excel 的 sheet 名
    TEXT = "text"  # 通用正文
    TABLE = "table"  # 表格 (Markdown 格式)
    LIST = "list"  # 列表项
    IMAGE = "image"  # 图片占位
    CODE = "code"  # 代码块
    ROW = "row"  # Excel 专用: 数据行
    SHEET_META = "sheet_meta"  # Excel 专用: 工作表元信息


@dataclass
class ElementMetadata:
    """元素元数据.

    统一收敛各格式解析器可能产出的附加信息，形成强类型约束。
    """

    # === 通用基础属性 ===
    original_label: str | None = None  # 解析器给的原始类别 (Title/NarrativeText/Table 等)
    original_type: str | None = None  # 原始类型
    text_length: int | None = None  # 内容字符数

    # === 视觉与排版属性 (主要来自 Word/PDF) ===
    bold: bool | None = None  # 段落是否整体加粗
    font_size: float | None = None  # 段落首 run 的字号 (pt)
    has_image: bool | None = None  # 段落内是否嵌入了图片
    is_down: bool | None = None  # 是否被判定为降级
    placeholder: bool | None = None  # 是否图片占位

    # === 判定与溯源属性 (主要用于 TITLE) ===
    match_source: str | None = None  # 标题来源: native_style / rule:<name> / heuristic / fallback
    match_debug: dict[str, Any] | None = None  # debug 模式下的判定轨迹

    # pdf解析属性
    page_number: int | None = None  # 页码


@dataclass
class Element:
    """解析阶段产出的原子元素.

    Attributes:
        type: 元素类型, 决定下游 chunker 如何处理.
        content: 元素的文本内容. 表格为 Markdown 字符串, 图片为占位符.
        level: 标题层级 (1-6). 仅 type 为 TITLE 时有效, 其他类型为 None.
        metadata: 格式特有的附加信息.

    Metadata 约定 key (Word 解析器输出):
        bold: bool                  段落是否整体加粗.
        font_size: float | None     段落首 run 的字号 (pt).
        text_length: int            内容字符数.
        original_label: str         解析器给的原始类别 (Title/NarrativeText/...).
        match_source: str           标题来源:
                                      native_style / rule:<name> / heuristic / fallback
        match_debug: dict           debug 模式下的判定轨迹 (可选).
    """

    type: ElementType
    content: str
    level: int | None = None  # 标题层级, 仅 TITLE 有效
    metadata: ElementMetadata = field(default_factory=ElementMetadata)
