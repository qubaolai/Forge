"""TaskOptions 合并链测试。"""

from __future__ import annotations

from pathlib import Path

from forge.config.settings import get_settings, reset_settings

from forge.adaptive.options import TaskOptions, TaskOptionsIn


def _reset_with_test_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("APP_CONFIG", raising=False)
    reset_settings()


def test_task_options_uses_sys_default(monkeypatch) -> None:
    _reset_with_test_env(monkeypatch)
    settings = get_settings()

    built = TaskOptions.build(None, settings=settings)

    assert built.allow_write is True
    assert built.allow_parallel is True
    assert built.max_agents == 4
    assert built.writer_mode == "isolated_worktree"
    assert built.verifier_cmd is None
    # N11: 未显式传 workspace_path 时不再兜底成 cwd，留空让入口路由做 400 校验
    assert built.workspace_path == ""
    reset_settings()


def test_task_options_user_override_and_caps(monkeypatch) -> None:
    _reset_with_test_env(monkeypatch)
    settings = get_settings()

    user_in = TaskOptionsIn(
        allow_write=False,
        allow_parallel=False,
        max_agents=8,
        writer_mode="direct",
        verifier_cmd="pytest -q",
        workspace_path=".",
    )
    built = TaskOptions.build(user_in, settings=settings)

    assert built.allow_write is False
    assert built.allow_parallel is False
    assert built.max_agents == 8
    assert built.writer_mode == "direct"
    assert built.verifier_cmd == "pytest -q"
    # 显式传入 "." 仍会被 expanduser().resolve() 规整为绝对路径
    assert built.workspace_path == str(Path(".").expanduser().resolve())
    reset_settings()


def test_task_options_max_agents_clamped_by_hard_caps(monkeypatch) -> None:
    _reset_with_test_env(monkeypatch)
    settings = get_settings()

    user_in = TaskOptionsIn(max_agents=8)
    built = TaskOptions.build(user_in, settings=settings)
    assert built.max_agents == min(8, built.hard_caps.max_agents)
    reset_settings()
