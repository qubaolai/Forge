"""Word 标题原始文本与自动编号还原.

解决两个问题:
    1. Unstructured 解析时会主动剥离 Heading 段落前的编号
       (例如 "3.2. 数据安全" 变成 "数据安全").
       做法: 用 python-docx 直接读 paragraph.text 取回完整文本.

    2. Word 多级列表自动编号时, 编号由渲染器生成, 不在 paragraph.text 里.
       做法: 解析 numbering.xml + 段落的 numPr, 维护跨段落计数器,
       还原出 "3.2." 这样的前缀.

       Word 的 numPr 有两种挂载方式, 都需要支持:
         a) 段落级 numPr: <w:p><w:pPr><w:numPr>...</w:numPr></w:pPr></w:p>
         b) 样式级 numPr: 挂在 Heading N 的样式定义里,
            所有引用该样式的段落自动继承.
       样式级还要处理 basedOn 继承链 (子样式继承父样式的 numPr).

提供两个能力:
    - HeadingTextResolver: 按文档顺序遍历, 给每个 Heading 段落输出 (level, full_text).
    - 供 Unstructured 路径按顺序对齐 Title 元素, 还原编号.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


# 简单的 numFmt 到字符的转换表
_DECIMAL_FMTS = {"decimal", "decimalZero", "decimalEnclosedCircle", "decimalEnclosedFullstop"}
_LOWER_LETTER_FMTS = {"lowerLetter"}
_UPPER_LETTER_FMTS = {"upperLetter"}
_LOWER_ROMAN_FMTS = {"lowerRoman"}
_UPPER_ROMAN_FMTS = {"upperRoman"}
_CHINESE_COUNTING = {"chineseCounting", "chineseCountingThousand", "ideographDigital"}


@dataclass
class HeadingItem:
    """文档中一个 Heading 段落的还原信息.

    Attributes:
        level: 标题层级 (1-6), 0 表示 Title 样式 (文档大标题).
        text: 段落原始文本 (paragraph.text).
        numbered_text: 加上还原编号后的文本; 无自动编号时等于 text.
        style_name: 原始样式名.
    """

    level: int
    text: str
    numbered_text: str
    style_name: str | None = None


class HeadingTextResolver:
    """按文档顺序解析所有 Heading 段落, 还原编号.

    使用流程:
        resolver = HeadingTextResolver(file_path)
        resolver.resolve()
        for item in resolver.headings:
            print(item.level, item.numbered_text)
        # 或者按顺序对齐 Unstructured 的 Title 元素:
        for i, ust_title in enumerate(unstructured_titles):
            restored = resolver.headings[i].numbered_text

    Attributes:
        headings: 所有 Heading 段落, 按文档顺序排列.
    """

    # Word 标题样式名识别: "Heading 1" / "标题 1" / "heading2"
    _STYLE_LEVEL_PATTERN = re.compile(r"(?:heading|标题)\s*(\d+)", re.IGNORECASE)

    def __init__(self, file_path: Path):
        """初始化.

        Args:
            file_path: .docx 文件路径.
        """
        self.file_path = file_path
        self.headings: list[HeadingItem] = []
        # numId/ilvl 计数器, key=(numId, ilvl), value=已经出现过的次数
        self._counters: dict[tuple[str, int], int] = {}
        # numbering.xml 解析缓存: numId -> {ilvl: NumLevel}
        self._num_format_cache: dict[str, dict[int, NumLevel]] = {}
        # 样式级 numPr 缓存: styleId -> (numId, ilvl)
        # None 值表示该样式确认没有 numPr (避免重复查询)
        self._style_num_pr_cache: dict[str, tuple[str, int] | None] = {}
        # 样式继承链缓存: styleId -> basedOn 的 styleId
        self._style_based_on: dict[str, str | None] = {}
        # styles.xml 是否已加载
        self._styles_loaded: bool = False

    def resolve(self) -> None:
        """执行解析, 填充 self.headings."""
        from docx import Document
        from docx.text.paragraph import Paragraph

        doc = Document(str(self.file_path))
        # 预加载 numbering 部件
        try:
            self._load_numbering(doc)
        except Exception as e:  # numbering 部件可能不存在
            logger.debug("numbering 部件不可用: %s", e)
        # 预加载 styles, 用于样式级 numPr 回退
        try:
            self._load_styles(doc)
        except Exception as e:
            logger.debug("styles 加载失败: %s", e)

        body = doc.element.body
        for child in body.iterchildren():
            if child.tag != qn("w:p"):
                continue
            para = Paragraph(child, doc)

            level = self._heading_level(para)
            if level is None:
                # 非 Heading 段落, 但仍要更新自动编号计数器
                # (因为 numId 是跨样式累计的, 跳过会导致编号错位)
                self._tick_numbering(para)
                continue

            text = para.text  # 保留前后空格信息, 后面再 strip
            num_prefix = self._compute_num_prefix(para)

            stripped = text.strip()
            if num_prefix and not self._text_already_has_prefix(stripped, num_prefix):
                full = f"{num_prefix} {stripped}".strip()
            else:
                full = stripped

            self.headings.append(
                HeadingItem(
                    level=level,
                    text=stripped,
                    numbered_text=full,
                    style_name=para.style.name if para.style else "",
                )
            )

        logger.info(
            "HeadingTextResolver: %s 共 %d 个 Heading",
            self.file_path.name,
            len(self.headings),
        )

    # ---------- 内部工具 ----------

    def _heading_level(self, para) -> int | None:
        """从段落样式名取标题层级.

        Returns:
            1-6 表示 Heading N; None 表示非 Heading 段落.
            Title 样式 (文档大标题) 也归为 1.
        """
        if para.style is None or para.style.name is None:
            return None
        name = para.style.name
        m = self._STYLE_LEVEL_PATTERN.search(name)
        if m:
            return min(max(int(m.group(1)), 1), 6)
        # "Title" 样式 (文档主标题), 归 H1
        if name.lower() == "title" or name == "标题":
            return 1
        return None

    def _load_numbering(self, doc) -> None:
        """加载 numbering.xml, 缓存格式信息."""
        try:
            num_part = doc.part.numbering_part
            if num_part is None:
                return
            element = num_part.element
        except (AttributeError, KeyError):
            return

        # num 节点: <w:num w:numId="X"><w:abstractNumId w:val="Y"/></w:num>
        # abstractNum: <w:abstractNum w:abstractNumId="Y"><w:lvl w:ilvl="0">...</w:lvl></w:abstractNum>
        abstract_map: dict[str, dict[int, NumLevel]] = {}
        for abs_node in element.findall(qn("w:abstractNum")):
            abs_id = abs_node.get(qn("w:abstractNumId"))
            if abs_id is None:
                continue
            levels: dict[int, NumLevel] = {}
            for lvl_node in abs_node.findall(qn("w:lvl")):
                ilvl_str = lvl_node.get(qn("w:ilvl"))
                if ilvl_str is None:
                    continue
                ilvl = int(ilvl_str)

                fmt_node = lvl_node.find(qn("w:numFmt"))
                fmt = fmt_node.get(qn("w:val")) if fmt_node is not None else "decimal"

                start_node = lvl_node.find(qn("w:start"))
                start = int(start_node.get(qn("w:val"))) if start_node is not None else 1

                lvl_text_node = lvl_node.find(qn("w:lvlText"))
                lvl_text = lvl_text_node.get(qn("w:val")) if lvl_text_node is not None else "%1."

                levels[ilvl] = NumLevel(ilvl=ilvl, fmt=fmt, start=start, lvl_text=lvl_text)
            abstract_map[abs_id] = levels

        for num_node in element.findall(qn("w:num")):
            num_id = num_node.get(qn("w:numId"))
            abs_ref = num_node.find(qn("w:abstractNumId"))
            if num_id is None or abs_ref is None:
                continue
            abs_id = abs_ref.get(qn("w:val"))
            if abs_id in abstract_map:
                self._num_format_cache[num_id] = abstract_map[abs_id]

        logger.debug("numbering 加载完成: %d 个 numId", len(self._num_format_cache))

    def _load_styles(self, doc) -> None:
        """加载 styles.xml, 缓存每个样式的 numPr 和 basedOn 信息.

        样式级 numPr 是 Word 多级编号的常见挂载方式: Heading 1~9 的样式
        定义里都挂上 numId, 段落只要引用样式就自动继承编号.

        Args:
            doc: python-docx Document.
        """
        try:
            styles_part = doc.part.styles
            element = styles_part.element
        except (AttributeError, KeyError):
            return

        # styles.xml 顶层是 <w:styles>, 子节点是 <w:style>
        for style_node in element.findall(qn("w:style")):
            style_id = style_node.get(qn("w:styleId"))
            if style_id is None:
                continue

            # basedOn 链: <w:basedOn w:val="ParentStyleId"/>
            pPr = style_node.find(qn("w:pPr"))
            based_on_node = style_node.find(qn("w:basedOn"))
            self._style_based_on[style_id] = (
                based_on_node.get(qn("w:val")) if based_on_node is not None else None
            )

            # 样式级 numPr: <w:style><w:pPr><w:numPr><w:numId/><w:ilvl/></w:numPr></w:pPr>
            num_pr_info: tuple[str, int] | None = None
            if pPr is not None:
                numPr = pPr.find(qn("w:numPr"))
                if numPr is not None:
                    numId_node = numPr.find(qn("w:numId"))
                    ilvl_node = numPr.find(qn("w:ilvl"))
                    if numId_node is not None:
                        num_id = numId_node.get(qn("w:val"))
                        ilvl = int(ilvl_node.get(qn("w:val"))) if ilvl_node is not None else 0
                        if num_id and num_id != "0":
                            num_pr_info = (num_id, ilvl)
            self._style_num_pr_cache[style_id] = num_pr_info

        self._styles_loaded = True
        styled = sum(1 for v in self._style_num_pr_cache.values() if v is not None)
        logger.debug(
            "styles 加载完成: 共 %d 个样式, 其中 %d 个含 numPr",
            len(self._style_num_pr_cache),
            styled,
        )

    def _resolve_style_num_pr(self, style_id: str) -> tuple[str, int] | None:
        """沿 basedOn 链向上查找样式的 numPr (含继承).

        Word 样式系统支持继承: Heading 2 通常 basedOn=Heading 1.
        如果子样式没声明 numPr, 沿链找到最近的祖先 numPr.

        Args:
            style_id: 样式 ID, 如 "Heading1" / "1".

        Returns:
            (numId, ilvl) 或 None.
        """
        visited: set[str] = set()
        current: str | None = style_id
        while current and current not in visited:
            visited.add(current)
            info = self._style_num_pr_cache.get(current)
            if info is not None:
                return info
            # 该样式没 numPr, 沿 basedOn 向上
            current = self._style_based_on.get(current)
        return None

    def _get_num_pr(self, para) -> tuple[str, int] | None:
        """提取段落的 numId / ilvl.

        优先级:
            1. 段落级 numPr (paragraph properties)
            2. 段落引用的样式定义中的 numPr (含 basedOn 继承)

        Returns:
            (numId, ilvl); 段落无任何自动编号时返回 None.
        """
        # 1. 段落级
        pPr = para._p.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                ilvl_node = numPr.find(qn("w:ilvl"))
                numId_node = numPr.find(qn("w:numId"))
                if numId_node is not None:
                    num_id = numId_node.get(qn("w:val"))
                    ilvl = int(ilvl_node.get(qn("w:val"))) if ilvl_node is not None else 0
                    if num_id and num_id != "0":
                        return (num_id, ilvl)

        # 2. 样式级回退
        if self._styles_loaded and para.style is not None:
            style_id = para.style.style_id
            if style_id:
                info = self._resolve_style_num_pr(style_id)
                if info is not None:
                    return info

        return None

    def _tick_numbering(self, para) -> None:
        """非 Heading 段落也要更新计数器, 避免后续 Heading 编号错位."""
        info = self._get_num_pr(para)
        if info is None:
            return
        self._increment_and_reset(info)

    def _compute_num_prefix(self, para) -> str:
        """计算段落应显示的自动编号前缀.

        例: numId=2, ilvl=1, lvlText="%1.%2.", 当前 (2, 0) 出现过 3 次,
            (2, 1) 出现过 2 次 (加这次共 3 次) → 返回 "3.3."

        Returns:
            编号字符串; 无自动编号或无法解析时返回空串.
        """
        info = self._get_num_pr(para)
        if info is None:
            return ""

        # 计数 +1
        self._increment_and_reset(info)
        num_id, ilvl = info

        levels = self._num_format_cache.get(num_id)
        if not levels or ilvl not in levels:
            return ""
        lvl = levels[ilvl]

        # 拼装 lvlText: %1, %2 ... 替换成各层级当前计数
        text = lvl.lvl_text
        for i in range(ilvl + 1):
            counter_key = (num_id, i)
            count = self._counters.get(counter_key, 0)
            # 如果上层计数器还没初始化, 用 start 值
            if count == 0 and i in levels:
                count = levels[i].start
            symbol = self._format_number(count, levels[i].fmt) if i in levels else str(count)
            text = text.replace(f"%{i + 1}", symbol)
        return text

    def _increment_and_reset(self, info: tuple[str, int]) -> None:
        """核心修复：计数加 1 的同时，必须重置所有子层级的计数器。"""
        num_id, ilvl = info

        # 当前层级计数 +1
        self._counters[info] = self._counters.get(info, 0) + 1

        # Word 最大支持 9 个层级 (ilvl: 0 到 8)
        # 铁律：上级递增时，所有下级必须清零
        for deeper_ilvl in range(ilvl + 1, 9):
            deeper_key = (num_id, deeper_ilvl)
            if deeper_key in self._counters:
                # 设为 0 后，_compute_num_prefix 中的逻辑会自动取 start 值(通常是1)
                self._counters[deeper_key] = 0

    @staticmethod
    def _format_number(n: int, fmt: str) -> str:
        """按 numFmt 把数字转成字符串."""
        if fmt in _DECIMAL_FMTS:
            return str(n)
        if fmt in _LOWER_LETTER_FMTS:
            return _to_letter(n, lower=True)
        if fmt in _UPPER_LETTER_FMTS:
            return _to_letter(n, lower=False)
        if fmt in _LOWER_ROMAN_FMTS:
            return _to_roman(n).lower()
        if fmt in _UPPER_ROMAN_FMTS:
            return _to_roman(n)
        if fmt in _CHINESE_COUNTING:
            return _to_chinese(n)
        return str(n)  # 未知格式回退

    @staticmethod
    def _text_already_has_prefix(text: str, prefix: str) -> bool:
        """判断 text 是否已经带了等价的编号前缀, 避免重复.

        例: text="3.2 数据安全", prefix="3.2." → 已带前缀.
        简化策略: 只对比纯数字+点序列.
        """
        norm_prefix = re.sub(r"\s+", "", prefix).rstrip(".、, ")
        if not norm_prefix:
            return False
        # 取 text 开头的数字+点序列
        m = re.match(r"^[\d.]+", text)
        if not m:
            return False
        norm_text = m.group(0).rstrip(".、, ")
        return norm_text == norm_prefix


@dataclass
class NumLevel:
    """numbering.xml 中一个 ilvl 的格式信息."""

    ilvl: int
    fmt: str
    start: int
    lvl_text: str  # 例如 "%1." 或 "%1.%2." 或 "(%1)"


def _to_letter(n: int, lower: bool = True) -> str:
    """1→a, 2→b, 26→z, 27→aa."""
    if n <= 0:
        return ""
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("a" if lower else "A") + rem) + s
    return s


_ROMAN_TABLE = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def _to_roman(n: int) -> str:
    """1→I, 4→IV, 9→IX, 14→XIV."""
    if n <= 0:
        return ""
    s = ""
    for v, sym in _ROMAN_TABLE:
        while n >= v:
            s += sym
            n -= v
    return s


_CN_DIGITS = "零一二三四五六七八九"


def _to_chinese(n: int) -> str:
    """1→一, 10→十, 11→十一, 23→二十三. 简化处理百以内."""
    if n == 0:
        return _CN_DIGITS[0]
    if n < 10:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return f"十{_CN_DIGITS[n - 10]}"
    if n < 100:
        tens, ones = divmod(n, 10)
        return f"{_CN_DIGITS[tens]}十" + (_CN_DIGITS[ones] if ones else "")
    return str(n)
