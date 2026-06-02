"""Word 文档解析器.

直接使用 python-docx 解析 .docx
判定流水线: 三阶段标题分类器 (原生 Heading 样式 → 规则引擎 → 启发式).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from forge.core.types import Element, ElementMetadata, ElementType
from forge.retrieval.common.format_profile import FormatProfile
from forge.retrieval.parsers.parser_base import BaseParser

from .numbering_restorer import HeadingTextResolver
from .style_detector import HeuristicTitleDetector
from .title_rules import (
    ClassifyTracer,
    RuleEngine,
    RuleSet,
    RuleSetLoader,
    TitleClassifier,
)

logger = logging.getLogger(__name__)

# 匹配典型的目录页码后缀，如: "...... 12", "… 1", "\t 2"
TOC_PAGE_PATTERN = re.compile(r"(\.{3,}|…+|\t+)\s*\d+$")

# 增加代码块识别
MONO_FONTS = {
    "courier new",
    "consolas",
    "monaco",
    "lucida console",
    "source code pro",
    "fira code",
    "menlo",
    "dejavu sans mono",
    "courier",
    "inconsolata",
    "roboto mono",
}

CODE_STYLE_KEYWORDS = {"code", "preformat", "pre", "mono", "代码", "程序"}

CODE_TEXT_PATTERNS = [
    re.compile(r"^\s*(def |class |import |from \S+ import )"),  # Python
    re.compile(r"^\s*(function |const |let |var |=>|async )"),  # JS/TS
    re.compile(r"^\s*(public|private|protected|void|static)\s"),  # Java/C#
    re.compile(r"^\s*(#include|int main|printf|scanf)"),  # C/C++
    re.compile(r"[{};]\s*$"),  # 行尾括号/分号
    re.compile(r"^\s{2,}\S"),  # 有明显缩进
    re.compile(r"(==|!=|<=|>=|&&|\|\||-&gt;|::|\+=|-=)"),  # 运算符组合
    re.compile(r"^\s*[#//].*"),  # 注释
    re.compile(r"`[^`]+`"),  # 行内代码
    re.compile(r"^\s*\$\s+\S"),  # shell 命令
]


# TODO 注意系统层配置文件要控制debug开关
class WordParser(BaseParser):
    """Word 文档解析器.

    输出标准化的 Element 列表, 下游 chunker 无需感知格式细节.

    Attributes:
        ruleset: 规则集. 为空规则集时只走原生样式 + 启发式兜底.
        debug: 是否记录判定轨迹.
        debug_dir: debug 文件输出目录.
    """

    def __init__(
        self,
        ruleset: RuleSet | None = None,
        rule_config_paths: list[Path] | None = None,
        debug: bool = False,
        debug_dir: Path | None = None,
    ):
        """初始化解析器.

        Args:
            ruleset: 已加载的规则集. 优先于 rule_config_paths.
            rule_config_paths: YAML 路径列表, 当 ruleset 为 None 时使用.
            debug: 是否启用 debug 轨迹.
            debug_dir: debug JSON 输出目录, 默认 data/debug/.
        """
        if ruleset is not None:
            self.ruleset = ruleset
        elif rule_config_paths:
            self.ruleset = RuleSetLoader().load(rule_config_paths)
        else:
            self.ruleset = RuleSet()  # 空规则集

        self.debug = debug
        self.debug_dir = debug_dir or Path("data/debug")

    # ---------- 主入口 ----------

    def parse(self, file_path: Path) -> list[Element]:
        """解析 Word 文档.

        Args:
            file_path: .docx 文件路径.

        Returns:
            Element 列表, 顺序与原文档一致.
        """
        doc = DocxDocument(str(file_path))

        # 构建格式画像 + 三阶段分类器
        profile = FormatProfile.calibrate(doc.paragraphs)
        engine = RuleEngine(self.ruleset, profile) if not self.ruleset.is_empty() else None
        fallback = HeuristicTitleDetector(profile)
        classifier = TitleClassifier(engine=engine, fallback=fallback)
        tracer = ClassifyTracer(self.debug)

        # 还原 Heading 段落的自动编号 (paragraph.text 不含)
        resolver = HeadingTextResolver(file_path)
        try:
            resolver.resolve()
        except Exception as e:
            logger.warning("HeadingTextResolver 失败, 使用 paragraph.text: %s", e)

        results: list[Element] = []
        title_buffer: list[Element] = []  # 连续标题缓存区
        code_buffer: list[str] = []  # 暂存连续代码段的文本

        def flush_titles(as_toc: bool = False):
            """将缓存的标题结算入 results。

            Args:
                as_toc: 是否因为层级断裂强制降级为普通正文(目录/空章节防误判)。
            """
            if not title_buffer:
                return
            for t in title_buffer:
                # 触发条件：强制降级 或 自身携带目录页码特征
                if as_toc or TOC_PAGE_PATTERN.search(t.content.strip()):
                    t.type = ElementType.TEXT
                    # if "original_type" not in t.metadata:
                    #     t.metadata["original_type"] = "TITLE"
                    t.metadata.is_down = True
                    t.metadata.match_source = "trigger_downgrade"
                results.append(t)
            title_buffer.clear()

        def flush_code():
            if not code_buffer:
                return
            results.append(
                Element(
                    type=ElementType.CODE,
                    content="\n".join(code_buffer),
                    metadata=ElementMetadata(original_label="Code"),
                )
            )
            code_buffer.clear()

        idx = 0
        heading_seq = 0  # 已处理 native Title 数量, 用于对齐 resolver.headings

        # 按文档真实顺序遍历段落和表格
        for child in self._iter_block_items(doc):
            # ---- 段落 ----
            if isinstance(child, Paragraph):
                idx += 1
                text = child.text.strip()
                has_image = self._para_has_image(child)

                # 空段落: 若含图片则保留占位
                if not text:
                    if has_image:
                        flush_titles(as_toc=False)  # 遇到图片，说明前面的多级标题有效
                        results.append(
                            Element(
                                type=ElementType.IMAGE,
                                content="[图片]",
                                metadata=ElementMetadata(placeholder=True, original_label="Image"),
                                # metadata={"placeholder": True, "original_label": "Image"},
                            )
                        )
                    continue
                if self._para_is_code(child):
                    flush_titles(as_toc=False)  # 代码前的标题视为合法
                    code_buffer.append(child.text)  # 保留原始缩进，不 strip
                    continue
                flush_code()

                bold = self._para_bold(child)
                font_size = self._para_font_size(child)
                style_label = self._native_label(child)

                # 对原生 Heading 样式段落, 预读层级 + 对齐 resolver
                native_level: int | None = None
                aligned_item = None
                if style_label == "Title":
                    item = resolver.headings[heading_seq]
                    if item.text.strip() == text or text in item.text:
                        aligned_item = item
                        if item.level >= 1:
                            native_level = item.level

                    if native_level is None:
                        native_level = fallback.detect_title_level(child)

                result = classifier.classify(
                    text=text,
                    bold=bold,
                    font_size=font_size,
                    original_label=style_label,
                    native_level=native_level,
                    para=child,
                )

                # 推进游标 + 还原编号
                final_text = text
                if style_label == "Title":
                    if aligned_item is not None:
                        final_text = aligned_item.numbered_text
                    heading_seq += 1

                tracer.record(
                    index=idx,
                    text=final_text,
                    bold=bold,
                    font_size=font_size,
                    original_label=style_label,
                    decision="title" if result.is_title else "text",
                    level=result.level,
                    source=result.source,
                    candidates=result.candidates,
                    matched_rule=result.rule_name,
                    score=result.score,
                    score_breakdown=result.score_breakdown,
                )

                if result.is_title:
                    elem = Element(
                        type=ElementType.TITLE,
                        content=final_text,
                        level=result.level,
                        metadata=ElementMetadata(
                            bold=bold,
                            font_size=font_size,
                            original_label=style_label,
                            match_source=result.source,
                            text_length=len(final_text),
                            has_image=has_image,
                        ),
                    )

                    # 目录与空章节防误判核心逻辑
                    if title_buffer:
                        last_title = title_buffer[-1]
                        # 兜底：若没有识别出 level 则赋一个大数值代表底层
                        curr_level = result.level if result.level is not None else 99
                        last_level = last_title.level if last_title.level is not None else 99

                        # 如果当前标题层级 <= 上一个标题层级 (例如连续同级 H2->H2 或反弹 H2->H1)
                        # 说明这俩标题之间没有写任何正文，属于异常连续标题，极大概率是人工目录
                        if curr_level <= last_level:
                            flush_titles(as_toc=True)

                    title_buffer.append(elem)

                else:
                    # 遇到正常正文内容，证明前面缓存的连续标题块(如 1 -> 1.1)是合法的
                    flush_titles(as_toc=False)
                    results.append(
                        Element(
                            type=ElementType.TEXT,
                            content=text,
                            metadata=ElementMetadata(
                                bold=bold,
                                font_size=font_size,
                                original_label=style_label,
                                match_source="fallback",
                                text_length=len(text),
                                has_image=has_image,
                            ),
                        )
                    )

            # ---- 表格 ----
            elif isinstance(child, Table):
                # 表格也属于正文载体，确认前面的连续标题块合法
                flush_titles(as_toc=False)
                flush_code()
                # 先判断是否是代码表格
                is_code, code_text = self._table_is_code_block(child)
                if is_code:
                    code_buffer.append(code_text)
                else:
                    md = self._docx_table_to_markdown(child)
                    if md:
                        results.append(
                            Element(
                                type=ElementType.TABLE,
                                content=md,
                                metadata=ElementMetadata(original_label="Table"),
                            )
                        )

        # 遍历结束，若最后还残留连续标题，将其作为无内容章节/目录作降级处理
        flush_titles(as_toc=True)
        flush_code()

        if self.debug:
            tracer.dump(self.debug_dir / f"{file_path.stem}.json", file_path.stem)

        title_count = sum(1 for e in results if e.type == ElementType.TITLE)
        logger.info("解析 %s: %d 元素 (含 %d 标题)", file_path.name, len(results), title_count)
        return results

    # ---------- 文档块遍历 ----------

    @staticmethod
    def _iter_block_items(doc) -> Iterator:
        """按文档真实顺序遍历段落和表格."""
        body = doc.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, doc)
            elif child.tag == qn("w:tbl"):
                yield Table(child, doc)

    # ---------- 段落特征工具 ----------

    @staticmethod
    def _para_bold(para: Paragraph) -> bool:
        """段落是否整体加粗 (所有非空 run 都加粗)."""
        runs = [r for r in para.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    @staticmethod
    def _para_font_size(para: Paragraph) -> float | None:
        """段落首 run 字号 (pt)."""
        for run in para.runs:
            if run.font.size is not None:
                return run.font.size.pt
        return None

    @staticmethod
    def _native_label(para: Paragraph) -> str | None:
        """从段落样式名推断 original_label."""
        if para.style is None or para.style.name is None:
            return None
        name = para.style.name.lower()
        if "heading" in name or "标题" in name:
            return "Title"
        return "NarrativeText"

    @staticmethod
    def _para_has_image(para: Paragraph) -> bool:
        """检测段落里是否嵌入了图片."""
        drawing_tag = qn("w:drawing")
        pict_tag = qn("w:pict")
        return any(elem.tag == drawing_tag or elem.tag == pict_tag for elem in para._p.iter())

    # ---------- 表格转 Markdown (含合并单元格处理) ----------

    @staticmethod
    def _docx_table_to_markdown(table: Table) -> str:
        """python-docx 的 Table 对象转 Markdown."""
        if not table.rows:
            return ""

        rows_text: list[list[str]] = []
        last_row_text: list[str] = []

        for row in table.rows:
            cells_text: list[str] = []
            for col_idx, cell in enumerate(row.cells):
                text = cell.text.strip().replace("|", "\\|").replace("\n", " ")
                if (
                    not text
                    and WordParser._is_vmerge_continue(cell)
                    and col_idx < len(last_row_text)
                ):
                    text = last_row_text[col_idx]
                cells_text.append(text)
            rows_text.append(cells_text)
            last_row_text = cells_text

        if not rows_text:
            return ""

        max_cols = max(len(r) for r in rows_text)
        lines: list[str] = []
        for i, row in enumerate(rows_text):
            padded = row + [""] * (max_cols - len(row))
            lines.append("| " + " | ".join(padded) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        return "\n".join(lines)

    @staticmethod
    def _is_vmerge_continue(cell) -> bool:
        """判断单元格是否是垂直合并的"延续"行."""
        tcPr = cell._tc.find(qn("w:tcPr"))
        if tcPr is None:
            return False
        vMerge = tcPr.find(qn("w:vMerge"))
        if vMerge is None:
            return False
        val = vMerge.get(qn("w:val"))
        return val is None or val == ""

    @staticmethod
    def _table_is_code_block(table: Table) -> tuple[bool, str]:
        if not table.rows:
            return False, ""

        all_texts = []
        for row in table.rows:
            cells = [
                cell.text.strip()
                for cell in row.cells
                if cell.text.strip() and not re.fullmatch(r"\d+", cell.text.strip())
            ]
            if cells:
                all_texts.extend(cells)

        if not all_texts:
            return False, ""

        full_text = "\n".join(all_texts)
        score = 0

        # ── 结构信号 ──────────────────────────────────────
        row_count = len(table.rows)
        # 去重后的列数（处理合并单元格导致的重复）
        col_count = max(len({cell._tc for cell in row.cells}) for row in table.rows)

        if row_count == 1 and col_count == 1:
            score += 3  # 单行单列，强信号
        elif col_count == 1:
            score += 3  # 多行单列，同样强信号
        else:
            score += 1  # 多列表格，弱信号

        # ── 背景色信号 ────────────────────────────────────
        has_shading = False
        for row in table.rows:
            for cell in row.cells:
                tcPr = cell._tc.find(qn("w:tcPr"))
                if tcPr is not None:
                    shd = tcPr.find(qn("w:shd"))
                    if shd is not None:
                        fill = shd.get(qn("w:fill"), "").upper()
                        if fill not in ("", "AUTO", "FFFFFF"):
                            has_shading = True
                            break
            if has_shading:
                break
        if has_shading:
            score += 2

        # ── 等宽字体占比 ──────────────────────────────────
        mono_cell_count = 0
        total_cell_count = 0
        for row in table.rows:
            for cell in row.cells:
                if not cell.text.strip():
                    continue
                total_cell_count += 1
                for para in cell.paragraphs:
                    for run in para.runs:
                        if (run.font.name or "").lower() in MONO_FONTS:
                            mono_cell_count += 1
                            break
        if total_cell_count > 0 and mono_cell_count / total_cell_count >= 0.6:
            score += 2

        # ── 内容 pattern（对每个单元格单独跑，任意命中即累加）──
        cell_texts = set()
        for row in table.rows:
            for cell in row.cells:
                t = cell.text.strip()
                if t and not re.fullmatch(r"\d+", t):
                    cell_texts.add(t)

        for t in cell_texts:
            hits = sum(1 for p in CODE_TEXT_PATTERNS if p.search(t))
            score += hits

        # ── 多行缩进结构 ──────────────────────────────────
        lines = full_text.splitlines()
        if len(lines) >= 2:
            indented = sum(1 for line in lines if re.match(r"^\s{2,}\S", line))
            if indented / len(lines) >= 0.5:
                score += 1

        return score >= 8, full_text

    @staticmethod
    def _para_is_code(para: Paragraph) -> bool:
        score = 0

        # ── 强信号，单独命中即可判定 ──────────────────────
        # 1. 样式名含代码关键字
        style_name = (para.style.name or "").lower() if para.style else ""
        if any(k in style_name for k in CODE_STYLE_KEYWORDS):
            score += 4

        # 2. 背景色为灰（作者专门设置了代码底色）
        pPr = para._p.find(qn("w:pPr"))
        if pPr is not None:
            shd = pPr.find(qn("w:shd"))
            if shd is not None:
                fill = shd.get(qn("w:fill"), "").upper()
                if fill not in ("", "AUTO", "FFFFFF"):
                    return True

        # ── 弱信号，需要组合 ──────────────────────────────
        text = para.text.strip()
        if not text:
            return False

        # 3. 等宽字体占比（弱信号，+2分）
        total_len = sum(len(r.text) for r in para.runs if r.text.strip())
        mono_len = sum(
            len(r.text)
            for r in para.runs
            if r.text.strip() and (r.font.name or "").lower() in MONO_FONTS
        )
        if total_len > 0 and mono_len / total_len >= 0.85:
            score += 2

        # 4. 内容 pattern 命中数（每命中1个 +1分）
        hits = sum(1 for p in CODE_TEXT_PATTERNS if p.search(text))
        score += hits

        # 5. 纯符号/缩进结构特征（+1分）
        lines = text.splitlines()
        if len(lines) >= 2:
            indented = sum(1 for line in lines if re.match(r"^\s{2,}\S", line))
            if indented / len(lines) >= 0.5:
                score += 1

        # 阈值：至少得 8 分才判定为代码
        # 即：样式(4) + 等宽字体(2) + 至少2个pattern命中(2) 才够
        # 纯等宽字体(2) 不够 → 解决误判问题
        return score >= 8
