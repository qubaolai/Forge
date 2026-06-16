"""跨平台本地路径解析.

单机部署形态下,所有用户数据 / 日志全部落在操作系统约定的用户目录,
通过 ``platformdirs`` 自动解析:

- macOS:   ``~/Library/Application Support/assistant/``
- Linux:   ``~/.local/share/assistant/``
- Windows: ``%APPDATA%\\assistant\\``

测试 / 临时环境可以通过环境变量 ``FORGE_HOME`` 强制指定根目录,
绕开 ``platformdirs`` (典型场景: pytest 跑 ``tmp_path``).
"""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs

__all__ = [
    # 全局根目录
    "app_data_dir",
    # KB 存储
    "kb_dir",
    "kb_db_path",
    "chroma_dir",
    "bm25_path",
    "uploads_dir",
    # Chat run 持久化
    "chat_runs_dir",
    "chat_run_dir",
    "chat_run_state_path",
    "chat_run_events_path",
    # 会话沙盒 (workspace)
    "workspace_dir",
    "session_workspace_dir",
    # 任务 / 日志 / 审计
    "tasks_db_path",
    "cost_log_path",
    "audit_log_path",
]

_APP = "assistant"
_ENV_OVERRIDE = "FORGE_HOME"


# ─────────────────────────────────────────────────────────────────────────────
# 全局根目录
# ─────────────────────────────────────────────────────────────────────────────


def app_data_dir() -> Path:
    """全局数据根目录. 启动时自动创建.

    优先级:
    1. 环境变量 ``FORGE_HOME`` (测试 / 容器 / 临时环境用)
    2. ``platformdirs.user_data_dir("assistant")`` (生产用户机器)
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return _mkdir(Path(override).expanduser())
    return _mkdir(Path(platformdirs.user_data_dir(_APP)))


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
# Chat run 持久化（events.jsonl + state.json，per assistant message_id）
# ─────────────────────────────────────────────────────────────────────────────


def chat_runs_dir() -> Path:
    """所有 chat turn 的事件流根目录。

    每个 assistant message_id 一个子目录：
        <chat_runs>/<message_id>/state.json
        <chat_runs>/<message_id>/events.jsonl

    保留策略：完成后 7 天清理（见 ChatTurnSupervisor）。
    """
    return _mkdir(app_data_dir() / "chat_runs")


def chat_run_dir(message_id: str) -> Path:
    """某条 assistant message 的事件流目录。"""
    return _mkdir(chat_runs_dir() / message_id)


def chat_run_state_path(message_id: str) -> Path:
    return chat_run_dir(message_id) / "state.json"


def chat_run_events_path(message_id: str) -> Path:
    return chat_run_dir(message_id) / "events.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# 会话沙盒 (workspace): 用户隔离 + 会话隔离的可写目录
#   <workspace>/<user_id>/<session_id>/<relpath>
#   承载: 用户上传的大段输入附件 + LLM write_file 工具产出的代码文件。
#   保留策略: 与会话同生命周期, 会话删除时整目录级联清理 (见 SessionService.delete)。
# ─────────────────────────────────────────────────────────────────────────────


def workspace_dir() -> Path:
    """所有会话沙盒的根目录。"""
    return _mkdir(app_data_dir() / "workspace")


def session_workspace_dir(user_id: str, session_id: str) -> Path:
    """某个用户某个会话的沙盒目录。启动时按需创建。"""
    return _mkdir(workspace_dir() / user_id / session_id)


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


# ─────────────────────────────────────────────────────────────────────────────
# 内部
# ─────────────────────────────────────────────────────────────────────────────


def _mkdir(p: Path) -> Path:
    """确保目录存在, 返回原路径. 失败时让 OSError 抛出 (上层决定处理)."""
    p.mkdir(parents=True, exist_ok=True)
    return p
