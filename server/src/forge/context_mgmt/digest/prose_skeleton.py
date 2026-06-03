"""prose_skeleton: prose 段 -> 结构化骨架 Segment 列表 (cheap, 同步, 无 LLM).

按 markdown 标题/段落把长 prose 切成语义子段, 每段取标题或首句作 anchor +
短预览作 digest_text, 供 read_message 按 line_range 定向回读。

与异步 LLM 版 (prose_summarizer) 共用同一个分段器 (segmenter.split_prose_sections),
两者锚点/行号一致 —— 冷启动先用本骨架兜底, 异步算完后无缝升级为逐段摘要。
"""

from __future__ import annotations

from forge.context_mgmt.digest.segmenter import (
    ProseSection,
    RawSegment,
    split_prose_sections,
)
from forge.context_mgmt.digest.types import Segment

# 单段预览最大字符数 (取标题后前若干非空行拼成)
_PREVIEW_MAX_CHARS = 200
# 锚点标签最大字符数
_ANCHOR_MAX_CHARS = 60
# 预览取的非空行数上限
_PREVIEW_LINES = 3


def build_prose_segments(raw: RawSegment) -> list[Segment]:
    """把一个 prose RawSegment 切成结构化骨架 Segment 列表 (空内容返回 [])。"""
    sections = split_prose_sections(raw.text, raw.start_line)
    out: list[Segment] = []
    for sec in sections:
        out.append(
            Segment(
                kind="prose",
                start_line=sec.start_line,
                end_line=sec.end_line,
                anchor=section_anchor(sec),
                digest_text=_make_preview(sec.heading, sec.text),
            )
        )
    return out


def section_anchor(sec: ProseSection) -> str:
    """prose 子段锚点 = 标题 (或首句) + 行号区间。

    供同步骨架 (build_prose_segments) 与异步 LLM 摘要 (prose_summarizer 路径) 共用,
    保证两条路径锚点一致 —— 缓存命中后从骨架平滑升级为摘要, 锚点不变。
    """
    return _make_anchor(sec.heading, sec.text, sec.start_line, sec.end_line)


def _first_nonempty(text: str) -> str:
    """返回首个非空行 (去除 markdown 标题前缀 #)。"""
    for ln in text.split("\n"):
        s = ln.strip().lstrip("#").strip()
        if s:
            return s
    return ""


def _make_anchor(heading: str | None, text: str, start: int, end: int) -> str:
    """锚点 = 标题 (或首句) + 行号区间, 供 LLM 定向回读。"""
    label = heading or _first_nonempty(text) or "段落"
    if len(label) > _ANCHOR_MAX_CHARS:
        label = label[: _ANCHOR_MAX_CHARS - 1] + "…"
    return f"{label} (L{start}-{end})"


def _make_preview(heading: str | None, text: str) -> str:
    """预览 = 标题之后的前若干非空行 (非 LLM, 仅截取)。"""
    body_lines: list[str] = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            continue
        # 跳过标题行本身 (避免预览重复标题)
        if heading and s.lstrip("#").strip() == heading:
            continue
        body_lines.append(s)
        if len(body_lines) >= _PREVIEW_LINES:
            break
    preview = " ".join(body_lines)
    if len(preview) > _PREVIEW_MAX_CHARS:
        preview = preview[:_PREVIEW_MAX_CHARS] + " …(截断, read_message 回读)"
    return preview
