"""测试 forge.config.settings 的环境变量替换逻辑."""

from __future__ import annotations

import os

import pytest
from forge.config.settings import _expand_env


def test_simple_substitution(monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    assert _expand_env("${MY_VAR}") == "hello"


def test_default_value(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    assert _expand_env("${MISSING:fallback}") == "fallback"


def test_empty_default(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    assert _expand_env("${MISSING:}") == ""


def test_missing_no_default_raises(monkeypatch):
    monkeypatch.delenv("MUST_SET", raising=False)
    with pytest.raises(RuntimeError, match="MUST_SET"):
        _expand_env("${MUST_SET}")


def test_nested_dict(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    out = _expand_env({"x": "${FOO}", "y": ["${FOO}", 42], "z": {"k": "${FOO}"}})
    assert out == {"x": "bar", "y": ["bar", 42], "z": {"k": "bar"}}


def test_non_string_passthrough():
    assert _expand_env(42) == 42
    assert _expand_env(None) is None
    assert _expand_env(True) is True


def test_multiple_vars_in_string(monkeypatch):
    monkeypatch.setenv("A", "x")
    monkeypatch.setenv("B", "y")
    assert _expand_env("${A}-${B}") == "x-y"


# ── dotenv 解析器测试 ────────────────────────────────────────────────────────
def test_dotenv_strips_inline_comments(tmp_path, monkeypatch):
    """COOKIE_SECURE=false  # comment → 应该只设置 'false'."""
    from forge.config.settings import _load_dotenv_if_present

    env_file = tmp_path / ".env"
    env_file.write_text("FOO=false              # 生产环境必须 true\n")
    monkeypatch.delenv("FOO", raising=False)

    _load_dotenv_if_present(tmp_path)
    assert os.environ.get("FOO") == "false"


def test_dotenv_quoted_value_preserves_hash(tmp_path, monkeypatch):
    """密码里有 # 时, 用引号包裹应被保留."""
    from forge.config.settings import _load_dotenv_if_present

    env_file = tmp_path / ".env"
    env_file.write_text('PASSWORD="pa#ss word"\n')
    monkeypatch.delenv("PASSWORD", raising=False)

    _load_dotenv_if_present(tmp_path)
    assert os.environ.get("PASSWORD") == "pa#ss word"


def test_dotenv_skips_full_line_comments(tmp_path, monkeypatch):
    from forge.config.settings import _load_dotenv_if_present

    (tmp_path / ".env").write_text("# this is a comment\nREAL=value\n")
    monkeypatch.delenv("REAL", raising=False)
    _load_dotenv_if_present(tmp_path)
    assert os.environ.get("REAL") == "value"


def test_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    """已设置的环境变量优先, .env 仅补缺."""
    from forge.config.settings import _load_dotenv_if_present

    (tmp_path / ".env").write_text("X=from_dotenv\n")
    monkeypatch.setenv("X", "from_shell")
    _load_dotenv_if_present(tmp_path)
    assert os.environ.get("X") == "from_shell"
