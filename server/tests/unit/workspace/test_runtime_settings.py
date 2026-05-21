from __future__ import annotations

from pathlib import Path

from forge.workspace.runtime import (
    resolve_runtime_settings,
    resolve_tool_runtime_policy,
)


def test_runtime_settings_defaults() -> None:
    cfg = resolve_runtime_settings(raw_settings={})
    assert cfg.chat_timeout_seconds == 300.0
    assert cfg.wall_clock_soft_limit_sec == 120.0
    assert cfg.wall_clock_warn_limit_sec == 180.0
    assert cfg.wall_clock_hard_limit_sec == 240.0
    assert cfg.tool_path_allowlist == (".",)
    assert cfg.shell_timeout_seconds == 300.0


def test_runtime_settings_override_values() -> None:
    cfg = resolve_runtime_settings(
        raw_settings={
            "chat_timeout_seconds": 45,
            "chat_wall_clock_soft_limit_sec": 30,
            "chat_wall_clock_warn_limit_sec": 50,
            "chat_wall_clock_hard_limit_sec": 80,
            "tool_path_allowlist": ["src", "/tmp"],
            "shell_timeout_seconds": 66,
        }
    )
    assert cfg.chat_timeout_seconds == 45.0
    assert cfg.wall_clock_soft_limit_sec == 30.0
    assert cfg.wall_clock_warn_limit_sec == 50.0
    assert cfg.wall_clock_hard_limit_sec == 80.0
    assert cfg.tool_path_allowlist == ("src", "/tmp")
    assert cfg.shell_timeout_seconds == 66.0


def test_runtime_settings_invalid_wall_clock_order_falls_back() -> None:
    cfg = resolve_runtime_settings(
        raw_settings={
            "chat_wall_clock_soft_limit_sec": 100,
            "chat_wall_clock_warn_limit_sec": 90,
            "chat_wall_clock_hard_limit_sec": 80,
        }
    )
    assert cfg.wall_clock_soft_limit_sec == 120.0
    assert cfg.wall_clock_warn_limit_sec == 180.0
    assert cfg.wall_clock_hard_limit_sec == 240.0


def test_resolve_tool_runtime_policy_builds_absolute_roots(tmp_path: Path) -> None:
    start = tmp_path / "repo"
    start.mkdir(parents=True)
    p = resolve_tool_runtime_policy(
        start=start,
        raw_settings={
            "tool_path_allowlist": ["src", str(tmp_path / "abs")],
            "shell_timeout_seconds": 15,
        },
    )
    assert p.shell_timeout_seconds == 15.0
    assert p.allowed_roots[0] == (start / "src").resolve()
    assert p.allowed_roots[1] == (tmp_path / "abs").resolve()
