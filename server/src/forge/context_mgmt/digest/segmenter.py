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
