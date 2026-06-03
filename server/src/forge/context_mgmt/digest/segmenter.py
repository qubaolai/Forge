"""segmenter: 把一条 assistant 消息按 ``` 围栏切成 prose / code 段.

输出 RawSegment 列表 (尚未生成 digest 文本):
    - prose 段: 围栏之外的普通文本 (走 LLM 摘要)。
    - code 段: 围栏之内的代码 (走 tree-sitter 代码骨架)。

行号约定: start_line / end_line 是 **原文 (整条消息 content) 中的 1-based 闭区间**,
保证 read_message(line_range) 能据此回读到原文对应行。code 段的范围是围栏之间的
代码体 (不含 ``` 行本身)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# 匹配围栏起始: 可带语言, 如 ```python / ~~~  。允许前导空白。
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+\-.#]*)[ \t]*$")


@dataclass
class RawSegment:
    """切分后的原始分段 (未生成 digest)."""

    kind: Literal["prose", "code"]
    start_line: int
    end_line: int
    text: str
    language: str | None = None


def split_segments(content: str) -> list[RawSegment]:
    """按围栏切分. 返回有序 RawSegment 列表 (空内容返回 [])。"""
    if not content:
        return []
    lines = content.split("\n")
    segments: list[RawSegment] = []

    prose_buf: list[str] = []
    prose_start = 1  # 当前 prose 缓冲在原文中的起始行号

    in_code = False
    fence_marker = ""        # 开围栏用的标记 (``` 或 ~~~), 闭合需同种
    code_lang: str | None = None
    code_buf: list[str] = []
    code_start = 0           # 代码体起始行号 (开围栏的下一行)

    def flush_prose(end_line: int) -> None:
        nonlocal prose_buf
        text = "\n".join(prose_buf)
        if text.strip():
            segments.append(
                RawSegment(kind="prose", start_line=prose_start,
                           end_line=end_line, text=text)
            )
        prose_buf = []

    for idx, line in enumerate(lines, start=1):
        m = _FENCE_RE.match(line)
        if not in_code and m:
            # 进入代码块: 先收尾 prose (到围栏前一行)
            flush_prose(idx - 1)
            in_code = True
            fence_marker = m.group(1)[0] * 3  # 归一化标记种类
            code_lang = (m.group(2) or "").strip().lower() or None
            code_buf = []
            code_start = idx + 1
            continue
        if in_code:
            # 闭合围栏 (同种标记, 无语言)
            cm = _FENCE_RE.match(line)
            if cm and cm.group(1)[0] * 3 == fence_marker and not cm.group(2):
                code_text = "\n".join(code_buf)
                end_line = idx - 1
                if code_text.strip():
                    segments.append(
                        RawSegment(
                            kind="code", start_line=code_start,
                            end_line=end_line, text=code_text, language=code_lang,
                        )
                    )
                in_code = False
                prose_buf = []
                prose_start = idx + 1
                continue
            code_buf.append(line)
            continue
        # 普通 prose 行
        if not prose_buf:
            prose_start = idx
        prose_buf.append(line)

    # 收尾: 文件结束时仍在 code (未闭合) 按代码段处理; 否则收 prose
    if in_code and code_buf:
        code_text = "\n".join(code_buf)
        if code_text.strip():
            segments.append(
                RawSegment(
                    kind="code", start_line=code_start,
                    end_line=len(lines), text=code_text, language=code_lang,
                )
            )
    elif prose_buf:
        flush_prose(len(lines))

    return segments


# ---------------------------------------------------------------------------
# prose 段内的语义子分段 (按 markdown 标题 / 段落切)
# ---------------------------------------------------------------------------

# ATX 标题: # ~ ###### + 空格 + 文本 (允许尾随 #)。要求 # 后必须有空白,
# 以避免把 "#tag" / "#!/bin/sh" 之类误判为标题。
_HEADING_RE = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


@dataclass
class ProseSection:
    """prose 段内的语义子段 (按 markdown 标题切分).

    heading: 子段起始的 markdown 标题文本 (无标题则 None)。
    start_line / end_line: 在 **原文** 中的绝对 1-based 闭区间。
    text: 子段原文 (含标题行)。
    """

    heading: str | None
    start_line: int
    end_line: int
    text: str


def split_prose_sections(text: str, base_line: int = 1) -> list[ProseSection]:
    """把一段 prose 文本按 markdown 标题切成语义子段.

    规则:
        - 以 ATX 标题行 (# ~ ######) 作为子段边界, 标题归入其后子段。
        - 标题前的内容自成一段 (heading=None)。
        - 无标题的整段不再细切 (保持整体, 由上层取首句作锚点)。

    行号: 返回 start_line/end_line 为原文绝对 1-based 闭区间
    (= base_line + 段内相对行号), 供 read_message(line_range) 定向回读。
    空内容返回 []。
    """
    if not text:
        return []
    lines = text.split("\n")
    sections: list[ProseSection] = []

    cur_start_rel = 0          # 当前子段相对起始行 (0-based)
    cur_heading: str | None = None
    cur_lines: list[str] = []

    def flush(end_rel: int) -> None:
        nonlocal cur_lines
        body = "\n".join(cur_lines)
        if body.strip():
            sections.append(
                ProseSection(
                    heading=cur_heading,
                    start_line=base_line + cur_start_rel,
                    end_line=base_line + end_rel,
                    text=body,
                )
            )
        cur_lines = []

    for rel, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            # 标题行: 先收尾上一子段 (到标题前一行), 再以标题开新子段
            if cur_lines:
                flush(rel - 1)
            cur_start_rel = rel
            cur_heading = m.group(2).strip()
            cur_lines = [line]
            continue
        if not cur_lines:
            cur_start_rel = rel
        cur_lines.append(line)

    if cur_lines:
        flush(len(lines) - 1)

    return sections
