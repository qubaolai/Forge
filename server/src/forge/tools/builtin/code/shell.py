"""执行本地 shell 命令的工具.

安全模型:
    - **危险命令黑名单**: 高风险模式直接拒绝 (rm -rf /, sudo, dd of=, mkfs,
      ``curl | sh``, fork bomb 等).
    - **超时**: 默认走 workspace 配置 (``shell_timeout_seconds``), 由 executor
      预处理时夹紧到 policy 允许范围.
    - **cwd 路径校验**: 由 ``ToolExecutor._apply_workspace_policy`` 兜底, 工具自身
      只跑命令.
    - **输出截断**: stdout / stderr 各保留前 ``max_output_chars`` 字符 (默认 10000).
    - **有副作用**, ``parallelism_safe=False``: 同 step 内串行 (避免抢锁 / 端口).

不在工具层做的事:
    - 沙箱隔离 (docker / chroot) — 留给 P5+ subprocess_sandbox.
    - 鉴权 — 单机模式无多用户.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 60.0
_DEFAULT_MAX_OUTPUT_CHARS = 10_000

# 危险命令模式 — 命中即拒绝执行
_BLOCKLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$)"),
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+~(\s|$|/)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bsu\s+-"),
    re.compile(r"\bchmod\s+(-R\s+)?[0-7]*777\b"),
    re.compile(r"\bdd\s+if=.*of=/dev/(sd|nvme|disk)"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r">\s*/dev/(sd|nvme|disk)"),
    # curl|wget pipe 直接 sh
    re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh)\b"),
    # fork bomb
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;"),
    # rm -rf $HOME / $USER 等敏感目录变量
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+\$\{?(HOME|USER|PWD)"),
)


def _is_blocked(command: str) -> str | None:
    """返回触发的危险模式描述; 安全则返回 None."""
    for pat in _BLOCKLIST_PATTERNS:
        if pat.search(command):
            return pat.pattern
    return None


@register_tool
class Shell(Tool):
    name = "shell"
    description = (
        "执行 shell 命令并返回 stdout/stderr/exit_code. 默认超时由 workspace 配置 "
        "决定, 命令前会做危险模式黑名单检查 (rm -rf /, sudo, curl|sh 等直接拒绝). "
        "stdout/stderr 各截断到 10000 字符."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令 (会过 /bin/sh -c)",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录 (绝对或相对当前). 必须在 workspace 范围内.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "超时秒数. 默认走 workspace 配置, 由 executor 夹紧.",
                "minimum": 0,
            },
            "max_output_chars": {
                "type": "integer",
                "description": f"stdout/stderr 各自截断长度. 默认 {_DEFAULT_MAX_OUTPUT_CHARS}.",
                "minimum": 100,
            },
        },
        "required": ["command"],
    }
    parallelism_safe = False
    dangerous = True
    audit_payload_fields = ("command", "cwd")
    timeout_sec = 300.0
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        command: str = args["command"]
        cwd_str: str | None = args.get("cwd")
        timeout: float = float(args.get("timeout_seconds") or _DEFAULT_TIMEOUT_SEC)
        max_output: int = int(args.get("max_output_chars") or _DEFAULT_MAX_OUTPUT_CHARS)

        blocked = _is_blocked(command)
        if blocked:
            logger.warning("Shell 危险命令被拒 command=%r matched=%s", command, blocked)
            return {
                "ok": False,
                "blocked": True,
                "reason": f"命令命中危险模式: {blocked}",
                "command": command,
            }

        cwd = self._resolve_cwd(cwd_str)
        env = os.environ.copy()

        logger.info(
            "Shell 执行 command=%s cwd=%s timeout=%.1fs",
            _preview(command, 200),
            cwd,
            timeout,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动失败: {exc}", "command": command}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "ok": False,
                "timeout": True,
                "command": command,
                "timeout_seconds": timeout,
            }

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        stdout_truncated = len(stdout) > max_output
        stderr_truncated = len(stderr) > max_output
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "stdout": stdout[:max_output],
            "stderr": stderr[:max_output],
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }

    @staticmethod
    def _resolve_cwd(cwd_str: str | None) -> Path | None:
        if not cwd_str:
            return None
        p = Path(cwd_str).expanduser()
        return (Path.cwd() / p).resolve() if not p.is_absolute() else p.resolve()


def _preview(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "...(truncated)"


__all__ = ["Shell", "_is_blocked", "_BLOCKLIST_PATTERNS"]
