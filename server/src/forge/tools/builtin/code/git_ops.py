"""git 只读操作工具.

P1 范围 (本工具): ``status / log / diff / blame / branch / show``.
P4 会再加 ``git_ops_write`` (commit / push / branch -d 等) 单独走 audit + HITL.

行为:
    - 命令通过 ``asyncio.create_subprocess_exec`` 启动, **不走 shell**, 避免注入.
    - ``cwd`` 默认 cwd; 路径越界由 executor 兜底.
    - 输出 stdout/stderr 各截到 ``max_output_chars`` (默认 20000).
    - 操作集白名单 + 参数白名单, 拒绝任何 ``--write`` / ``--exec`` / ``--upload-pack`` 等
      可能引爆危险的旁路.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 30.0

# 操作集合 -> 该操作允许的参数前缀白名单
_ALLOWED_OPS: dict[str, tuple[str, ...]] = {
    "status": ("-s", "--short", "-b", "--branch", "--porcelain"),
    "log": (
        "-n",
        "--max-count",
        "--oneline",
        "--graph",
        "--all",
        "--since",
        "--until",
        "--author",
        "--",
        "--stat",
        "--name-only",
        "--pretty",
    ),
    "diff": ("--cached", "--staged", "HEAD", "--stat", "--name-only", "--", "main", "master"),
    "blame": ("-L", "--show-name", "--", "-l"),
    "branch": ("-a", "--all", "-v", "-vv", "--list", "--show-current"),
    "show": ("--stat", "--name-only", "--pretty", "HEAD"),
    "rev-parse": ("--show-toplevel", "--abbrev-ref", "HEAD", "--git-dir"),
    "config": ("--get", "--local", "--global"),
}

# 在所有 op 里都禁止出现的危险参数子串
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "--upload-pack",
    "--receive-pack",
    "--exec",
    "ext::",
    "--config-env",
    "--upload-archive",
)


@register_tool
class GitOps(Tool):
    name = "git_ops"
    description = (
        "执行 git 只读子命令: status/log/diff/blame/branch/show/rev-parse/config. "
        "白名单内的参数才允许; 写操作 (commit/push/branch -d 等) 由 P4 的 git_ops_write 提供."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "description": "git 子命令名",
                "enum": sorted(_ALLOWED_OPS.keys()),
            },
            "args": {
                "type": "array",
                "description": "传给 git 子命令的参数列表 (按白名单过滤)",
                "items": {"type": "string"},
            },
            "cwd": {
                "type": "string",
                "description": "工作目录, 默认 cwd",
            },
            "max_output_chars": {
                "type": "integer",
                "description": f"stdout/stderr 各自截断长度. 默认 {_DEFAULT_MAX_OUTPUT_CHARS}.",
                "minimum": 100,
            },
        },
        "required": ["op"],
    }
    required_scope = "workspace"
    timeout_sec = _DEFAULT_TIMEOUT

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        op: str = args["op"]
        extra: list[str] = list(args.get("args") or [])
        cwd_str: str | None = args.get("cwd")
        max_output: int = int(args.get("max_output_chars") or _DEFAULT_MAX_OUTPUT_CHARS)

        if op not in _ALLOWED_OPS:
            return {"ok": False, "error": f"op 不在白名单: {op!r}"}

        allowed_prefixes = _ALLOWED_OPS[op]
        for token in extra:
            if not isinstance(token, str):
                return {"ok": False, "error": f"参数必须是字符串, 收到 {type(token).__name__}"}
            if any(bad in token for bad in _FORBIDDEN_SUBSTRINGS):
                return {"ok": False, "error": f"参数命中禁用子串: {token!r}"}
            # 允许两类:
            #   1. 以白名单前缀开头的 (--cached / -n=5 / HEAD~1)
            #   2. 看起来是 commit hash / path / branch (无前导 -)
            if token.startswith("-") and not _has_allowed_prefix(token, allowed_prefixes):
                return {
                    "ok": False,
                    "error": (
                        f"参数 {token!r} 不在 {op} 的允许列表里; 允许前缀: {list(allowed_prefixes)}"
                    ),
                }

        cwd = _resolve(cwd_str)

        cmd = ["git", op, *extra]
        logger.info("git_ops 执行 op=%s args=%s cwd=%s", op, extra, cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_DEFAULT_TIMEOUT
            )
        except FileNotFoundError:
            return {"ok": False, "error": "未安装 git"}
        except TimeoutError:
            return {"ok": False, "error": f"超时 ({_DEFAULT_TIMEOUT}s)"}
        except OSError as exc:
            return {"ok": False, "error": f"启动失败: {exc}"}

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "op": op,
            "args": extra,
            "cwd": str(cwd) if cwd else None,
            "stdout": stdout[:max_output],
            "stderr": stderr[:max_output],
            "stdout_truncated": len(stdout) > max_output,
            "stderr_truncated": len(stderr) > max_output,
        }


def _has_allowed_prefix(token: str, prefixes: tuple[str, ...]) -> bool:
    return any(token == pref or token.startswith(pref + "=") for pref in prefixes)


def _resolve(cwd_str: str | None) -> Path | None:
    if not cwd_str:
        return None
    p = Path(cwd_str).expanduser()
    return (Path.cwd() / p).resolve() if not p.is_absolute() else p.resolve()
