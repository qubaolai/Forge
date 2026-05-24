"""环境变量展开工具 — 供 settings.py 加载 yaml 时使用."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """递归替换 dict/list/str 中的 ${VAR} / ${VAR:default} 占位符.

    严格模式: 没有默认值且环境变量未设置 → 抛 RuntimeError, 启动失败.
    带默认值: ${VAR:default} 在变量缺失时返回 default (空字符串也允许).
    """
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            env_val = os.environ.get(var)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            raise RuntimeError(
                f"配置引用的环境变量未设置: ${{{var}}} (未提供默认值). "
                f"请检查 .env 或部署环境."
            )
        return _ENV_VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _parse_dotenv(path: Path) -> list[tuple[str, str]]:
    """解析 .env 文件, 返回 (key, value) 列表, 保留顺序.

    解析规则:
    - 支持 KEY=VALUE, 忽略空行和 # 整行注释
    - 支持单/双引号包裹的值 (引号内 # 不视为注释)
    - 未加引号时, ' #' (空格+#) 视为行内注释起点
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if not v:
            pairs.append((k, ""))
            continue
        if (v[0] == '"' and v[-1] == '"' and len(v) >= 2) or \
           (v[0] == "'" and v[-1] == "'" and len(v) >= 2):
            v = v[1:-1]
        else:
            idx = v.find(" #")
            if idx == -1:
                idx = v.find("\t#")
            if idx >= 0:
                v = v[:idx].rstrip()
        pairs.append((k, v))
    return pairs


def load_env_files(root: Path, app_env: str | None = None) -> None:
    """按优先级加载 .env 系列文件, 不覆盖系统已有的环境变量.

    加载顺序 (优先级从高到低):
        系统/Shell 已有变量   ← 永远不覆盖
        .env.{app_env}        ← 环境专属 (dev / test / prod)
        .env                  ← 公共基础默认值

    实现: 先强制写入 .env.{app_env} (仅限系统未设置的 key),
          再用 setdefault 写入 .env 填补剩余空缺.
    """
    # 1. 先收集系统启动时已有的 key (在任何 .env 加载之前)
    system_keys: set[str] = set(os.environ.keys())

    # 2. .env.{app_env} — 环境专属, 对系统 key 之外的 key 强制写入
    if app_env:
        env_file = root / f".env.{app_env}"
        if env_file.exists():
            for k, v in _parse_dotenv(env_file):
                if k not in system_keys:
                    os.environ[k] = v   # 覆盖 .env 同名 key, 但不覆盖系统 key

    # 3. .env — 公共基础, setdefault 只填补尚未设置的 key
    base_file = root / ".env"
    if base_file.exists():
        for k, v in _parse_dotenv(base_file):
            os.environ.setdefault(k, v)


def load_dotenv_if_present(root: Path) -> None:
    """向后兼容入口 — 只加载 .env, 不读取 APP_ENV."""
    load_env_files(root, app_env=None)
