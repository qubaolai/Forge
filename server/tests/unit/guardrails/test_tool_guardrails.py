"""permission_check + dangerous_op_blocker 单测."""

from __future__ import annotations

from forge.guardrails.tool.dangerous_op_blocker import DangerousOpBlocker
from forge.guardrails.tool.permission_check import PermissionChecker

# ──────────────────────────────────────────────────────────────────────
# PermissionChecker
# ──────────────────────────────────────────────────────────────────────


def test_permission_default_local_role_allows_everything() -> None:
    c = PermissionChecker()
    assert c.check("shell", "local", {"command": "ls"}).allow is True
    assert c.check("write_file", "local", {"path": "/tmp/x"}).allow is True


def test_permission_blocks_unlisted_tool_for_role() -> None:
    c = PermissionChecker()
    r = c.check("shell", "ra", {"command": "ls"})
    assert r.allow is False
    assert "无权" in r.reason


def test_permission_allows_listed_tool_for_role() -> None:
    c = PermissionChecker()
    assert c.check("read_file", "ra", {"path": "/tmp/x"}).allow is True
    assert c.check("write_file", "developer", {"path": "src/main.py"}).allow is True


def test_permission_path_whitelist_enforced(tmp_path) -> None:
    c = PermissionChecker(
        path_role_whitelist={"developer": [str(tmp_path / "src")]},
        workspace_root=tmp_path,
    )
    # 在白名单内
    ok = c.check(
        "write_file",
        "developer",
        {"path": str(tmp_path / "src" / "main.py")},
    )
    assert ok.allow is True
    # 不在白名单内
    bad = c.check(
        "write_file",
        "developer",
        {"path": str(tmp_path / "outside.py")},
    )
    assert bad.allow is False
    assert "不允许" in bad.reason


def test_permission_unknown_role_defaults_to_allow() -> None:
    c = PermissionChecker()
    assert c.check("shell", "no_such_role", {"command": "ls"}).allow is True


# ──────────────────────────────────────────────────────────────────────
# DangerousOpBlocker
# ──────────────────────────────────────────────────────────────────────


def test_blocker_passes_safe_shell() -> None:
    b = DangerousOpBlocker()
    assert b.check("shell", {"command": "ls -la"}).allow is True


def test_blocker_catches_rm_rf_root() -> None:
    b = DangerousOpBlocker()
    r = b.check("shell", {"command": "rm -rf /"})
    assert r.allow is False
    assert "危险" in r.reason


def test_blocker_passes_normal_write() -> None:
    b = DangerousOpBlocker()
    assert b.check("write_file", {"path": "/tmp/safe.txt"}).allow is True


def test_blocker_catches_system_write() -> None:
    b = DangerousOpBlocker()
    for p in ("/etc/passwd", "/usr/bin/python", "/System/Library/x", "/dev/sda"):
        r = b.check("write_file", {"path": p})
        assert r.allow is False, f"应拒: {p}"
        assert "系统目录" in r.reason


def test_blocker_catches_suspicious_http_host() -> None:
    b = DangerousOpBlocker()
    r = b.check("http_request", {"url": "https://x.dnslog.cn/?id=1"})
    assert r.allow is False
    assert "可疑" in r.reason


def test_blocker_passes_normal_http() -> None:
    b = DangerousOpBlocker()
    assert b.check("http_request", {"url": "https://api.example.com/v1"}).allow is True


def test_blocker_unrelated_tool_passes() -> None:
    b = DangerousOpBlocker()
    assert b.check("read_file", {"path": "/etc/hosts"}).allow is True
    assert b.check("knowledge_search", {"query": "x"}).allow is True
