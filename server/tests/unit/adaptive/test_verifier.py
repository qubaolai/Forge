"""Verifier 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.adaptive.verifier import Verifier

pytestmark = pytest.mark.asyncio


async def test_verifier_skips_when_command_none(tmp_path: Path) -> None:
    result = await Verifier().run(None, workspace_path=str(tmp_path))
    assert result.passed is True
    assert result.skipped is True
    assert result.returncode == 0


async def test_verifier_reports_success(tmp_path: Path) -> None:
    result = await Verifier().run("echo ok", workspace_path=str(tmp_path))
    assert result.passed is True
    assert result.returncode == 0
    assert "ok" in result.stdout


async def test_verifier_reports_failure(tmp_path: Path) -> None:
    result = await Verifier().run("exit 3", workspace_path=str(tmp_path))
    assert result.passed is False
    assert result.returncode == 3
