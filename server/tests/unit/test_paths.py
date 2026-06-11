"""config/paths.py 单元测试.

主要验证:
- FORGE_HOME 覆盖能强制全局根目录到 tmp_path
- 各路径函数返回值都在 FORGE_HOME 下
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import paths


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """所有路径函数走 tmp_path/assistant_home, 避免污染真实用户目录."""
    home = tmp_path / "assistant_home"
    monkeypatch.setenv("FORGE_HOME", str(home))
    return home


def test_app_data_dir_uses_env_override(_isolate_home: Path) -> None:
    assert paths.app_data_dir() == _isolate_home
    assert _isolate_home.is_dir()


def test_known_subpaths_all_under_data_dir(_isolate_home: Path) -> None:
    candidates = [
        paths.kb_db_path(),
        paths.chroma_dir(),
        paths.bm25_path(),
        paths.uploads_dir(),
        paths.tasks_db_path(),
        paths.cost_log_path(),
        paths.audit_log_path(),
        paths.chat_runs_dir(),
        paths.chat_run_state_path("msg_x"),
        paths.chat_run_events_path("msg_x"),
    ]
    for p in candidates:
        assert str(p).startswith(str(_isolate_home))
