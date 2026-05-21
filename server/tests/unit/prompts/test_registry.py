"""PromptRegistry 单测.

覆盖:
    1. 启动期扫到模板 + 渲染基本变量
    2. 模板找不到 -> 走兜底, 不抛
    3. 模板里变量未传 (StrictUndefined) -> 走兜底, 不抛
    4. 模板语法错误 -> 启动期立刻抛
    5. exists() 行为
    6. init_registry / get_registry / reset_registry 单例语义
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.prompts import (
    PromptRegistry,
    TemplateRenderError,
    get_registry,
    init_registry,
    reset_registry,
)


# ---------------------------------------------------------------------------
# Fixtures: 临时 prompts/ 目录, 避免依赖仓库根
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_prompts(tmp_path: Path) -> Path:
    root = tmp_path / "prompts"
    (root / "chat").mkdir(parents=True)
    (root / "chat" / "hello.j2").write_text("你好 {{ name }}, 今天是 {{ date }}.", encoding="utf-8")
    (root / "chat" / "with_if.j2").write_text(
        "{% if user_prompt %}USER:{{ user_prompt }}{% endif %}\nFRAMEWORK_TAIL.",
        encoding="utf-8",
    )
    return root


@pytest.fixture(autouse=True)
def _reset_registry_after_each_test():
    """每个用例结束清空全局单例, 避免污染."""
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# 1. 基本渲染
# ---------------------------------------------------------------------------
def test_render_basic(tmp_prompts: Path):
    reg = PromptRegistry(tmp_prompts)
    out = reg.render("chat/hello", name="alice", date="2026-05-16")
    assert out == "你好 alice, 今天是 2026-05-16."


def test_render_with_if_block(tmp_prompts: Path):
    reg = PromptRegistry(tmp_prompts)
    out_with = reg.render("chat/with_if", user_prompt="be terse")
    assert "USER:be terse" in out_with
    assert "FRAMEWORK_TAIL." in out_with

    out_without = reg.render("chat/with_if", user_prompt="")
    assert "USER:" not in out_without
    assert "FRAMEWORK_TAIL." in out_without


# ---------------------------------------------------------------------------
# 2. 模板找不到 -> 兜底
# ---------------------------------------------------------------------------
def test_missing_template_returns_fallback(tmp_prompts: Path):
    reg = PromptRegistry(tmp_prompts)
    out = reg.render("does/not/exist", name="x", date="y")
    assert out
    assert "helpful" in out  # 兜底文本含此关键词


# ---------------------------------------------------------------------------
# 3. 变量未传 (StrictUndefined) -> 兜底
# ---------------------------------------------------------------------------
def test_undefined_variable_returns_fallback(tmp_prompts: Path):
    reg = PromptRegistry(tmp_prompts)
    out = reg.render("chat/hello", name="alice")  # date 缺失
    assert "helpful" in out  # 走兜底, 不抛


# ---------------------------------------------------------------------------
# 4. 模板语法错误 -> 启动期 (构造时) 立刻抛
# ---------------------------------------------------------------------------
def test_template_syntax_error_raises_on_init(tmp_path: Path):
    root = tmp_path / "prompts"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "syntax.j2").write_text("{% if %}", encoding="utf-8")  # 不完整 if
    with pytest.raises(TemplateRenderError, match="syntax"):
        PromptRegistry(root)


# ---------------------------------------------------------------------------
# 5. exists()
# ---------------------------------------------------------------------------
def test_exists(tmp_prompts: Path):
    reg = PromptRegistry(tmp_prompts)
    assert reg.exists("chat/hello") is True
    assert reg.exists("chat/with_if") is True
    assert reg.exists("nope") is False


# ---------------------------------------------------------------------------
# 6. 全局单例语义
# ---------------------------------------------------------------------------
def test_init_registry_returns_same_singleton(tmp_prompts: Path):
    reg1 = init_registry(tmp_prompts)
    reg2 = get_registry()
    assert reg1 is reg2


def test_get_registry_lazy_init_uses_default_path(monkeypatch, tmp_prompts: Path):
    # 用环境变量指向临时目录, 不显式 init_registry, 验证 get_registry 懒初始化
    monkeypatch.setenv("PROMPTS_DIR", str(tmp_prompts))
    reg = get_registry()
    assert reg.exists("chat/hello")


def test_reset_registry_drops_singleton(tmp_prompts: Path):
    init_registry(tmp_prompts)
    reset_registry()
    # 重新设置环境变量并 lazy init, 应得到新实例
    import os

    os.environ["PROMPTS_DIR"] = str(tmp_prompts)
    try:
        reg = get_registry()
        assert reg.exists("chat/hello")
    finally:
        os.environ.pop("PROMPTS_DIR", None)
