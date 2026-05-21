"""R8 Resume 辅助函数单测.

覆盖三个独立工具:
    - _suffix_anchor          : 取末尾 N 字符
    - _inside_unclosed_fence  : 判断结束在未闭合的代码块
    - _inside_unclosed_table_row: 判断结束在未闭合的表格行
    - _render_resume_prompt   : 模板渲染贯通 (含结构感知)
"""

from __future__ import annotations

from forge.chat.resumer import (
    SUFFIX_ANCHOR_CHARS,
    _inside_unclosed_fence,
    _inside_unclosed_table_row,
    _render_resume_prompt,
    _suffix_anchor,
)


# ---------------------------------------------------------------------------
# _suffix_anchor
# ---------------------------------------------------------------------------
def test_suffix_anchor_empty_returns_empty() -> None:
    assert _suffix_anchor("") == ""
    assert _suffix_anchor("", 10) == ""


def test_suffix_anchor_short_returns_all() -> None:
    assert _suffix_anchor("hi", 10) == "hi"


def test_suffix_anchor_long_returns_tail() -> None:
    text = "x" * 200
    assert _suffix_anchor(text, 80) == "x" * 80
    assert _suffix_anchor(text) == "x" * SUFFIX_ANCHOR_CHARS


# ---------------------------------------------------------------------------
# _inside_unclosed_fence
# ---------------------------------------------------------------------------
def test_unclosed_fence_no_backticks_is_false() -> None:
    assert _inside_unclosed_fence("纯文本回答") is False


def test_unclosed_fence_one_open_is_true() -> None:
    text = "代码示例:\n```python\ndef foo():\n    return"
    assert _inside_unclosed_fence(text) is True


def test_unclosed_fence_paired_is_false() -> None:
    text = "代码示例:\n```python\nprint('hi')\n```\n说明: ..."
    assert _inside_unclosed_fence(text) is False


def test_unclosed_fence_three_pairs_open_again_is_true() -> None:
    # 第一段闭合, 第二段又开了一个没闭合
    text = "```py\na\n```\n说明\n```js\nx"
    assert _inside_unclosed_fence(text) is True


def test_unclosed_fence_empty_text() -> None:
    assert _inside_unclosed_fence("") is False


# ---------------------------------------------------------------------------
# _inside_unclosed_table_row
# ---------------------------------------------------------------------------
def test_unclosed_table_no_pipe_is_false() -> None:
    assert _inside_unclosed_table_row("纯文本") is False


def test_unclosed_table_complete_row_is_false() -> None:
    """整齐的表格行 (以 | 结尾) 不算未闭合."""
    text = "| a | b | c |"
    assert _inside_unclosed_table_row(text) is False


def test_unclosed_table_truncated_mid_row_is_true() -> None:
    """末尾以 | 开头但没以 | 结尾 -> 截断中间."""
    text = "| a | b | c |\n| 1 | 2"
    assert _inside_unclosed_table_row(text) is True


def test_unclosed_table_only_pipe_is_true() -> None:
    assert _inside_unclosed_table_row("|") is True


# ---------------------------------------------------------------------------
# _render_resume_prompt (模板贯通测试)
# ---------------------------------------------------------------------------
def test_render_resume_includes_anchor_and_basic_rules() -> None:
    prev = "我已经写了一段回答: 1. 第一点 2. 第二点 3."
    out = _render_resume_prompt(prev)
    # 末尾锚点应当在 prompt 中
    assert prev[-30:] in out
    # 核心规则关键词
    assert "不要重复" in out
    assert "续写规则" in out


def test_render_resume_no_prev_no_anchor() -> None:
    out = _render_resume_prompt("")
    # 没 prev 时不应出现锚点章节
    assert "你已输出内容的末尾" not in out


def test_render_resume_in_code_block_adds_hint() -> None:
    """末尾在未闭合代码块时, 模板应包含代码块特别提示."""
    prev = "```python\ndef foo():\n    return"
    out = _render_resume_prompt(prev)
    assert "代码块内部" in out


def test_render_resume_in_table_adds_hint() -> None:
    prev = "| 列 1 | 列 2 |\n|---|---|\n| a | b"
    out = _render_resume_prompt(prev)
    # "表格行中间" 是 in_table hint 特有短语 (普通规则只说 "表格行")
    assert "表格行中间" in out


def test_render_resume_complete_paragraph_no_structure_hint() -> None:
    prev = "这是一段完整的文字回答, 没有代码块也没有表格."
    out = _render_resume_prompt(prev)
    assert "代码块内部" not in out  # in_code_block hint 特有
    assert "表格行中间" not in out  # in_table hint 特有
