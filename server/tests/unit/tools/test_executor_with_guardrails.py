"""ToolExecutor 与 guardrails / audit log 的端到端集成测试."""

from __future__ import annotations

import tempfile

import pytest

from forge.core.types.message import ToolCall
from forge.tools.executor import ToolExecutor


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch):
    """每个测试用例独立 ASSISTANT_HOME, 隔离 audit.jsonl."""
    tmp = tempfile.mkdtemp(prefix="executor_audit_")
    monkeypatch.setenv("ASSISTANT_HOME", tmp)
    yield


def _call(name: str, args: dict) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=args)


@pytest.mark.asyncio
async def test_executor_blocks_dangerous_shell_and_audits() -> None:
    from forge.infrastructure.audit_log import default_audit_log

    exe = ToolExecutor()
    msg = await exe.aexecute(_call("shell", {"command": "rm -rf /"}))
    assert "tool blocked" in msg.content

    audit = default_audit_log()
    entries = await audit.filter(tool="shell", outcome="blocked")
    assert len(entries) == 1
    assert "rm -rf" in (entries[0].args_summary.get("command") or "")


@pytest.mark.asyncio
async def test_executor_rejects_path_outside_workspace() -> None:
    """workspace policy 是第一道闸: 写到 cwd 之外应被 ToolValidationError 直接拒."""
    from forge.core.types.errors import ToolValidationError

    exe = ToolExecutor()
    with pytest.raises(ToolValidationError):
        await exe.aexecute(_call("write_file", {"path": "/etc/passwd", "content": "x"}))


@pytest.mark.asyncio
async def test_executor_allows_safe_shell_and_audits_executed() -> None:
    from forge.infrastructure.audit_log import default_audit_log

    exe = ToolExecutor()
    msg = await exe.aexecute(_call("shell", {"command": "echo hello"}))
    assert "hello" in msg.content

    audit = default_audit_log()
    entries = await audit.filter(tool="shell", outcome="executed")
    assert len(entries) == 1
    assert entries[0].duration_ms is not None


@pytest.mark.asyncio
async def test_executor_does_not_audit_safe_tools() -> None:
    """非 dangerous 工具 (list_directory) 调用后 audit.jsonl 不应有记录."""
    from forge.infrastructure.audit_log import default_audit_log

    exe = ToolExecutor()
    msg = await exe.aexecute(_call("list_directory", {"path": "."}))
    assert msg.role == "tool"

    audit = default_audit_log()
    entries = [e async for e in audit.iter_all()]
    assert entries == []  # list_directory 不走 audit
