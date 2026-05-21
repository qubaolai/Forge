"""跨平台本地路径解析 — 单机化改造引入.

单机部署形态下,所有用户数据 / 配置 / 日志全部落在操作系统约定的用户目录,
通过 ``platformdirs`` 自动解析:

- macOS:   ``~/Library/Application Support/assistant/``
- Linux:   ``~/.local/share/assistant/``
- Windows: ``%APPDATA%\\assistant\\``

测试 / 临时环境可以通过环境变量 ``ASSISTANT_HOME`` 强制指定根目录,
绕开 ``platformdirs`` (典型场景: pytest 跑 ``tmp_path``).

详细方案见 ``单机化改造方案.md`` 第七节.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import platformdirs

__all__ = [
    # 全局根目录与文件
    "app_data_dir",
    "app_config_dir",
    "app_config_path",
    "app_lock_path",
    # 项目短暂状态
    "projects_dir",
    "project_state_dir",
    "workflow_runs_dir",
    "workflow_run_dir",
    "workflow_state_path",
    "workflow_events_path",
    "find_workflow_run_dir",
    # KB 存储
    "kb_dir",
    "kb_db_path",
    "chroma_dir",
    "bm25_path",
    "uploads_dir",
    # 任务 / 日志 / 审计
    "tasks_db_path",
    "cost_log_path",
    "audit_log_path",
    "logs_dir",
    "local_token_path",
    # 工作空间内文件
    "workspace_dotdir",
    "workspace_config_path",
    "workspace_settings_path",
    "workspace_local_settings_path",
    "workspace_assistant_path",
    "global_workspace_dir",
    "global_workspace_config_path",
    "global_workspace_settings_path",
    "global_workspace_local_settings_path",
    "global_workspace_assistant_path",
    "find_workspace_root",
    "encode_workspace_path",
]

_APP = "assistant"
_ENV_OVERRIDE = "ASSISTANT_HOME"


# ─────────────────────────────────────────────────────────────────────────────
# 全局根目录
# ─────────────────────────────────────────────────────────────────────────────


def app_data_dir() -> Path:
    """全局数据根目录. 启动时自动创建.

    优先级:
    1. 环境变量 ``ASSISTANT_HOME`` (测试 / 容器 / 临时环境用)
    2. ``platformdirs.user_data_dir("assistant")`` (生产用户机器)
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return _mkdir(Path(override).expanduser())
    return _mkdir(Path(platformdirs.user_data_dir(_APP)))


def app_config_dir() -> Path:
    """全局配置目录 (config.yaml 等用户编辑的配置).

    Mac / Linux 与 data_dir 不同 (``~/.config/assistant/``);
    Windows 上 platformdirs 默认与 data_dir 一致.

    被 ASSISTANT_HOME 覆盖时, 与 data_dir 合一 (测试场景简单).
    """
    if os.environ.get(_ENV_OVERRIDE):
        return app_data_dir()
    return _mkdir(Path(platformdirs.user_config_dir(_APP)))


def app_config_path() -> Path:
    """全局 config.yaml 路径."""
    return app_config_dir() / "config.yaml"


def app_lock_path() -> Path:
    """启动文件锁路径 (filelock 用, 防多进程同时跑)."""
    return app_data_dir() / "app.lock"


# ─────────────────────────────────────────────────────────────────────────────
# 项目短暂状态 (~/.assistant/projects/<encoded>/)
# ─────────────────────────────────────────────────────────────────────────────


def projects_dir() -> Path:
    """所有 workspace 短暂状态根目录."""
    return _mkdir(app_data_dir() / "projects")


def project_state_dir(workspace_root: Path) -> Path:
    """某个 workspace 的短暂状态目录.

    路径编码规则与 Claude Code 兼容: 把绝对路径里的 ``/`` / ``\\`` / ``:`` 替换成 ``-``.
    例如:
        /Users/almond/code/ec  ->  Users-almond-code-ec
        C:\\Users\\almond\\code -> C--Users-almond-code

    如果用户工作目录路径太长 (> 200 字符), 末尾追加 8 位 hash 防文件名超限.
    """
    encoded = encode_workspace_path(workspace_root)
    return _mkdir(projects_dir() / encoded)


def workflow_runs_dir(workspace_root: Path) -> Path:
    """某个 workspace 的 workflow 根目录."""
    return _mkdir(project_state_dir(workspace_root) / "workflows")


def workflow_run_dir(workspace_root: Path, workflow_id: str) -> Path:
    """某个 workflow 的状态目录."""
    return _mkdir(workflow_runs_dir(workspace_root) / workflow_id)


def workflow_state_path(workspace_root: Path, workflow_id: str) -> Path:
    """workflow state.json 路径."""
    return workflow_run_dir(workspace_root, workflow_id) / "state.json"


def workflow_events_path(workspace_root: Path, workflow_id: str) -> Path:
    """workflow events.jsonl 路径."""
    return workflow_run_dir(workspace_root, workflow_id) / "events.jsonl"


def find_workflow_run_dir(workflow_id: str) -> Path | None:
    """在全局 projects 下查找 workflow 目录."""
    root = projects_dir()
    if not root.exists():
        return None
    for p in root.glob(f"*/workflows/{workflow_id}"):
        if p.is_dir():
            return p
    return None


def encode_workspace_path(p: Path) -> str:
    """绝对路径 → 文件名安全的编码 (与 Claude Code 行为对齐)."""
    abs_str = str(p.expanduser().resolve())
    safe = abs_str.replace("/", "-").replace("\\", "-").replace(":", "-").lstrip("-")
    if len(safe) > 200:
        # 尾部追加短 hash, 保留前缀可读性
        suffix = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:8]
        safe = safe[:191] + "-" + suffix
    return safe


# ─────────────────────────────────────────────────────────────────────────────
# KB 存储 (Chroma / BM25 / 上传文件 / SQLite 元数据)
# ─────────────────────────────────────────────────────────────────────────────


def kb_dir() -> Path:
    return _mkdir(app_data_dir() / "kb")


def kb_db_path() -> Path:
    """KB 元数据 SQLite 路径 (knowledge_bases / kb_documents / kb_document_chunks)."""
    return kb_dir() / "kb.db"


def chroma_dir() -> Path:
    return _mkdir(kb_dir() / "chroma")


def bm25_path() -> Path:
    """BM25 SQLite FTS5 路径."""
    return _mkdir(kb_dir() / "bm25") / "bm25.db"


def uploads_dir() -> Path:
    """KB 文件上传根目录, 子结构: ``<kb_id>/<document_id>/<filename>``."""
    return _mkdir(kb_dir() / "uploads")


# ─────────────────────────────────────────────────────────────────────────────
# 任务 / 日志 / 审计
# ─────────────────────────────────────────────────────────────────────────────


def tasks_db_path() -> Path:
    """LocalTaskQueue 的 SQLite 路径 (local_tasks 状态机表)."""
    return app_data_dir() / "tasks.db"


def cost_log_path() -> Path:
    """LLM 调用成本流水 JSONL."""
    return app_data_dir() / "cost.jsonl"


def audit_log_path() -> Path:
    """危险工具调用审计 JSONL."""
    return app_data_dir() / "audit.jsonl"


def logs_dir() -> Path:
    """应用滚动日志目录."""
    return _mkdir(app_data_dir() / "logs")


def local_token_path() -> Path:
    """本机 HTTP 鉴权 token 文件 (替代 JWT, 见方案 10.7).

    文件权限应为 0600, 由调用方在写入时设置.
    """
    return app_data_dir() / "local_token"


# ─────────────────────────────────────────────────────────────────────────────
# 工作空间内文件 (<workspace_root>/.assistant/)
# ─────────────────────────────────────────────────────────────────────────────


def workspace_dotdir(workspace_root: Path) -> Path:
    """``<workspace>/.assistant/``. 不存在时自动创建."""
    return _mkdir(workspace_root / ".assistant")


def workspace_config_path(workspace_root: Path) -> Path:
    """``<workspace>/.assistant/workspace.json``."""
    return workspace_dotdir(workspace_root) / "workspace.json"


def workspace_settings_path(workspace_root: Path) -> Path:
    """``<workspace>/.assistant/settings.json``."""
    return workspace_dotdir(workspace_root) / "settings.json"


def workspace_local_settings_path(workspace_root: Path) -> Path:
    """``<workspace>/.assistant/settings.local.json``."""
    return workspace_dotdir(workspace_root) / "settings.local.json"


def workspace_assistant_path(workspace_root: Path) -> Path:
    """``<workspace>/.assistant/ASSISTANT.md``."""
    return workspace_dotdir(workspace_root) / "ASSISTANT.md"


def global_workspace_dir() -> Path:
    """无 workspace 场景的全局兜底目录."""
    return _mkdir(app_data_dir() / "workspace")


def global_workspace_config_path() -> Path:
    """全局兜底 ``workspace.json`` 路径."""
    return global_workspace_dir() / "workspace.json"


def global_workspace_settings_path() -> Path:
    """全局兜底 ``settings.json`` 路径."""
    return global_workspace_dir() / "settings.json"


def global_workspace_local_settings_path() -> Path:
    """全局兜底 ``settings.local.json`` 路径."""
    return global_workspace_dir() / "settings.local.json"


def global_workspace_assistant_path() -> Path:
    """全局兜底 ``ASSISTANT.md`` 路径."""
    return global_workspace_dir() / "ASSISTANT.md"


def find_workspace_root(start: Path) -> Path | None:
    """从 ``start`` 向上查找含 ``.assistant/`` 的目录.

    类似 ``git rev-parse --show-toplevel`` 的逻辑.
    找不到返回 ``None``, 调用方决定走 "全局兜底 workspace" 还是报错.
    """
    cur = start.expanduser().resolve()
    while True:
        if (cur / ".assistant").is_dir():
            return cur
        if cur.parent == cur:  # 到达文件系统根
            return None
        cur = cur.parent


# ─────────────────────────────────────────────────────────────────────────────
# 内部
# ─────────────────────────────────────────────────────────────────────────────


def _mkdir(p: Path) -> Path:
    """确保目录存在, 返回原路径. 失败时让 OSError 抛出 (上层决定处理)."""
    p.mkdir(parents=True, exist_ok=True)
    return p
