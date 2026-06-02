"""config/paths.py 单元测试.

主要验证:
- ASSISTANT_HOME 覆盖能强制全局根目录到 tmp_path
- 各路径函数返回值都在 ASSISTANT_HOME 下
- encode_workspace_path 行为符合预期 (/ \\ : → -, 长路径加 hash)
- find_workspace_root 向上找 .assistant/ 的逻辑
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import paths


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """所有路径函数走 tmp_path/.assistant_home, 避免污染真实用户目录."""
    home = tmp_path / "assistant_home"
    monkeypatch.setenv("ASSISTANT_HOME", str(home))
    return home


def test_app_data_dir_uses_env_override(_isolate_home: Path) -> None:
    assert paths.app_data_dir() == _isolate_home
    assert _isolate_home.is_dir()


def test_app_config_path_collapses_with_data_dir_under_override(
    _isolate_home: Path,
) -> None:
    """有 ASSISTANT_HOME 时, config 与 data 合一 (测试场景简单)."""
    assert paths.app_config_path() == _isolate_home / "config.yaml"


def test_known_subpaths_all_under_data_dir(_isolate_home: Path) -> None:
    candidates = [
        paths.kb_db_path(),
        paths.chroma_dir(),
        paths.bm25_path(),
        paths.uploads_dir(),
        paths.tasks_db_path(),
        paths.cost_log_path(),
        paths.audit_log_path(),
        paths.logs_dir(),
        paths.local_token_path(),
        paths.app_lock_path(),
        paths.global_workspace_config_path(),
        paths.global_workspace_settings_path(),
        paths.global_workspace_local_settings_path(),
        paths.global_workspace_assistant_path(),
    ]
    for p in candidates:
        assert str(p).startswith(str(_isolate_home))


def test_project_state_dir_encoding(_isolate_home: Path, tmp_path: Path) -> None:
    """workspace 路径被编码 (/ → -) 后挂到 projects/ 下."""
    ws = tmp_path / "code" / "ec"
    ws.mkdir(parents=True)
    state = paths.project_state_dir(ws)

    assert state.parent == paths.projects_dir()
    # 编码后路径不应含 / 或 \
    name = state.name
    assert "/" not in name
    assert "\\" not in name


def test_encode_workspace_path_replaces_separators(tmp_path: Path) -> None:
    ws = tmp_path / "a" / "b" / "c"
    ws.mkdir(parents=True)
    encoded = paths.encode_workspace_path(ws)
    assert "/" not in encoded
    assert "\\" not in encoded
    assert encoded.endswith("a-b-c")


def test_encode_workspace_path_long_path_gets_hash_suffix() -> None:
    """超长路径在尾部追加 sha1 hash 短码 (不要求路径真存在)."""
    # 构造 > 200 字符的虚拟路径, 不需要真 mkdir
    long_segments = ["segment" for _ in range(40)]
    p = Path("/").joinpath(*long_segments)
    encoded = paths.encode_workspace_path(p)
    assert len(encoded) <= 200
    # 应包含 8 位 hex hash 后缀
    assert encoded[-9] == "-"
    assert all(c in "0123456789abcdef" for c in encoded[-8:])


def test_find_workspace_root_returns_dir_with_dotassistant(tmp_path: Path) -> None:
    root = tmp_path / "myrepo"
    (root / "src" / "deep").mkdir(parents=True)
    (root / ".assistant").mkdir()

    found = paths.find_workspace_root(root / "src" / "deep")
    assert found == root


def test_find_workspace_root_returns_none_if_absent(tmp_path: Path) -> None:
    deep = tmp_path / "nothing" / "here"
    deep.mkdir(parents=True)
    assert paths.find_workspace_root(deep) is None


def test_workspace_dotdir_auto_creates(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    dot = paths.workspace_dotdir(root)
    assert dot.is_dir()
    assert dot == root / ".assistant"


def test_workspace_file_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert paths.workspace_config_path(root) == root / ".assistant" / "workspace.json"
    assert paths.workspace_settings_path(root) == root / ".assistant" / "settings.json"
    assert paths.workspace_local_settings_path(root) == (
        root / ".assistant" / "settings.local.json"
    )
    assert paths.workspace_assistant_path(root) == root / ".assistant" / "ASSISTANT.md"
