"""code_skeleton: 代码块 -> 骨架 Segment (cheap, 同步, 无 LLM).

优先用 tree-sitter (tree-sitter-language-pack 预编译多语言 grammar) 抽取 top-level
函数/类签名 + 行号区间; 该依赖缺失或语言不识别时, 降级为「首若干行 + 总行数」启发式。

行号: 入参 start_line 是代码体在 **原文** 中的起始行 (1-based), 抽出的相对行号会
被映射回原文绝对行号, 便于 read_message(line_range) 定向回读。
"""

from __future__ import annotations

import logging

from forge.context_mgmt.digest.segmenter import RawSegment
from forge.context_mgmt.digest.types import Segment

logger = logging.getLogger(__name__)

# 围栏语言别名 -> tree-sitter-language-pack 语言名
_LANG_ALIASES = {
    "py": "python", "python": "python",
    "js": "javascript", "javascript": "javascript", "jsx": "javascript",
    "ts": "typescript", "typescript": "typescript", "tsx": "tsx",
    "go": "go", "golang": "go",
    "rs": "rust", "rust": "rust",
    "java": "java",
    "c": "c", "h": "c",
    "cpp": "cpp", "c++": "cpp", "cc": "cpp",
    "cs": "c_sharp", "csharp": "c_sharp",
    "rb": "ruby", "ruby": "ruby",
    "php": "php",
    "kt": "kotlin", "kotlin": "kotlin",
    "swift": "swift",
    "scala": "scala",
    "sh": "bash", "bash": "bash", "shell": "bash",
    "sql": "sql",
}

# 视为「定义」的节点类型关键字 (跨语言模糊匹配)
_DEF_KEYWORDS = (
    "function", "method", "class", "interface",
    "struct", "impl", "enum", "module", "trait",
)

# 启发式: 降级时保留的前导行数
_HEURISTIC_HEAD_LINES = 12


def build_code_segment(raw: RawSegment) -> Segment:
    """把一个 code RawSegment 转成骨架 Segment。"""
    language = raw.language
    code = raw.text
    start_line = raw.start_line
    end_line = raw.end_line

    sigs = _extract_signatures(language, code, start_line)
    label = f"{language or 'code'} block (L{start_line}-{end_line})"

    # digest_text 不再重复 label —— label 已作为 anchor, Segment.render 会渲染 [anchor]
    if sigs:
        digest_text = "\n".join(f"L{s}-{e}: {sig}" for sig, s, e in sigs)
    else:
        digest_text = _heuristic_skeleton(code)

    return Segment(
        kind="code",
        start_line=start_line,
        end_line=end_line,
        anchor=label,
        digest_text=digest_text,
    )


def _extract_signatures(
    language: str | None, code: str, base_line: int
) -> list[tuple[str, int, int]]:
    """用 tree-sitter 抽 top-level 定义签名. 返回 [(签名, 绝对起始行, 绝对结束行)]。

    依赖缺失 / 语言不识别 / 解析异常时返回 []。
    """
    if not language:
        return []
    ts_lang = _LANG_ALIASES.get(language.lower())
    if not ts_lang:
        return []
    try:
        from tree_sitter_language_pack import get_parser
    except Exception:  # noqa: BLE001 — 依赖未安装, 走启发式
        return []
    try:
        parser = get_parser(ts_lang)
        tree = parser.parse(code.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("tree-sitter 解析失败 lang=%s: %s", ts_lang, exc)
        return []

    code_lines = code.split("\n")
    out: list[tuple[str, int, int]] = []

    def is_def(node) -> bool:
        return any(k in node.type for k in _DEF_KEYWORDS)

    def visit(node, depth: int) -> None:
        for child in node.children:
            if is_def(child):
                rel_start = child.start_point[0]  # 0-based
                rel_end = child.end_point[0]
                sig = code_lines[rel_start].strip() if rel_start < len(code_lines) else child.type
                # 签名行过长时截断
                if len(sig) > 160:
                    sig = sig[:157] + "..."
                out.append((sig, base_line + rel_start, base_line + rel_end))
            elif depth < 1:
                # 仅再下探一层 (覆盖 export/decorated_definition 等包裹节点)
                visit(child, depth + 1)

    visit(tree.root_node, 0)
    return out


def _heuristic_skeleton(code: str) -> str:
    """无 tree-sitter 时的降级: 前若干行 + 总行数 (label 由 anchor 承载, 此处不重复)。"""
    lines = code.split("\n")
    head = list(lines[:_HEURISTIC_HEAD_LINES])
    preview = "\n".join(head)
    more = max(0, len(lines) - len(head))
    suffix = f"\n... (共 {len(lines)} 行, 余 {more} 行折叠; 用 read_message 回读)" if more else ""
    return f"{preview}{suffix}"
