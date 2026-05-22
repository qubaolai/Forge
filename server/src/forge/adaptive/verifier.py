"""质量验证器（M8）。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class VerifyResult:
    """验证结果。"""

    passed: bool
    command: str | None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    skipped: bool = False
    timed_out: bool = False


class Verifier:
    """执行质量门禁命令。"""

    async def run(
        self,
        command: str | None,
        *,
        workspace_path: str,
        timeout_sec: int = 600,
    ) -> VerifyResult:
        if command is None or not command.strip():
            return VerifyResult(
                passed=True,
                command=command,
                returncode=0,
                skipped=True,
            )

        started = time.perf_counter()
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            duration_ms = int((time.perf_counter() - started) * 1000)
            return VerifyResult(
                passed=False,
                command=command,
                returncode=None,
                stdout="",
                stderr=f"验证命令超时（>{timeout_sec}s）",
                duration_ms=duration_ms,
                timed_out=True,
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return VerifyResult(
            passed=(proc.returncode == 0),
            command=command,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )
