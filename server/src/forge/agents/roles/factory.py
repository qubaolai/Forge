"""角色注册表: 预置 7 角色 + 动态扩展钩子."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from config.settings import get_settings


@dataclass(frozen=True)
class AgentRole:
    name: str
    prompt_template: str
    allowed_tools: tuple[str, ...]
    model_preference: str | None
    fallback_model: str | None
    max_steps: int
    can_write: bool
    write_path_prefixes: tuple[str, ...]


def _builtin_roles() -> dict[str, AgentRole]:
    # 角色 × 模型矩阵来自 CLAUDE.md 3.3 节.
    return {
        "triage": AgentRole(
            name="triage",
            prompt_template="roles/triage",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "list_directory",
                "glob_search",
                "grep",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="sonnet-4-6",
            fallback_model="gpt-4o",
            max_steps=10,
            can_write=False,
            write_path_prefixes=(),
        ),
        "developer": AgentRole(
            name="developer",
            prompt_template="roles/developer",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "write_file",
                "edit_file",
                "list_directory",
                "glob_search",
                "grep",
                "git_ops",
                "shell",
                "http_request",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="sonnet-4-6",
            fallback_model="gpt-4o",
            max_steps=30,
            can_write=True,
            write_path_prefixes=("src/", "app/", "lib/"),
        ),
        "architect": AgentRole(
            name="architect",
            prompt_template="roles/architect",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "list_directory",
                "glob_search",
                "grep",
                "git_ops",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="opus-4-7",
            fallback_model="sonnet-4-6",
            max_steps=15,
            can_write=False,
            write_path_prefixes=(),
        ),
        "reviewer": AgentRole(
            name="reviewer",
            prompt_template="roles/reviewer",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "list_directory",
                "glob_search",
                "grep",
                "git_ops",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="opus-4-7",
            fallback_model="sonnet-4-6",
            max_steps=15,
            can_write=False,
            write_path_prefixes=(),
        ),
        "qa": AgentRole(
            name="qa",
            prompt_template="roles/qa",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "list_directory",
                "glob_search",
                "grep",
                "shell",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="gpt-4o",
            fallback_model="gpt-4o-mini",
            max_steps=20,
            can_write=True,
            write_path_prefixes=("tests/",),
        ),
        "ra": AgentRole(
            name="ra",
            prompt_template="roles/ra",
            allowed_tools=(
                "knowledge_search",
                "read_file",
                "list_directory",
                "glob_search",
                "grep",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="gpt-4o",
            fallback_model="gpt-4o-mini",
            max_steps=10,
            can_write=False,
            write_path_prefixes=(),
        ),
        "devops": AgentRole(
            name="devops",
            prompt_template="roles/devops",
            allowed_tools=(
                "read_file",
                "write_file",
                "edit_file",
                "list_directory",
                "glob_search",
                "grep",
                "git_ops",
                "shell",
                "http_request",
                "create_artifact",
                "get_artifact",
                "search_artifact",
                "delegate_to_agent",
                "spawn_subagent",
            ),
            model_preference="gpt-4o-mini",
            fallback_model="gpt-4o",
            max_steps=25,
            can_write=True,
            write_path_prefixes=(".github/", "docker/", "infra/"),
        ),
    }


AGENT_ROLES: dict[str, AgentRole] = _builtin_roles()
_CUSTOM_ROLES: dict[str, AgentRole] = {}
_ROLE_LOCK = Lock()


def get_agent_role(name: str) -> AgentRole:
    key = (name or "").strip().lower()
    with _ROLE_LOCK:
        if key in _CUSTOM_ROLES:
            return _CUSTOM_ROLES[key]
    if key in AGENT_ROLES:
        return AGENT_ROLES[key]
    raise KeyError(f"未知角色: {name}")


def list_agent_roles(*, include_custom: bool = True) -> dict[str, AgentRole]:
    with _ROLE_LOCK:
        if not include_custom:
            return dict(AGENT_ROLES)
        return {**AGENT_ROLES, **_CUSTOM_ROLES}


def register_custom_agent_role(role: AgentRole, *, overwrite: bool = False) -> None:
    """注册动态角色扩展点.

    约束:
    - 仅影响当前进程生命周期.
    - 默认不覆盖预置角色.
    """
    key = role.name.strip().lower()
    if not key:
        raise ValueError("角色名不能为空")
    with _ROLE_LOCK:
        if key in AGENT_ROLES and not overwrite:
            raise ValueError(f"角色 {key!r} 是预置角色, overwrite=False 不允许覆盖")
        if key in _CUSTOM_ROLES and not overwrite:
            raise ValueError(f"角色 {key!r} 已存在自定义定义")
        _CUSTOM_ROLES[key] = role


def reset_custom_agent_roles() -> None:
    with _ROLE_LOCK:
        _CUSTOM_ROLES.clear()


def resolve_runtime_model_id(role: AgentRole) -> str | None:
    """解析当前环境可用的角色模型.

    role 配置里的 model_preference / fallback_model 可能是目标态名称 (例如 sonnet-4-6),
    当前环境不一定已配置. 这里仅在可解析时返回, 否则返回 None 让调用方走默认模型.
    """
    settings = get_settings()
    candidates = [role.model_preference, role.fallback_model]
    seen: set[str] = set()
    for candidate in candidates:
        c = (candidate or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        if ":" in c:
            provider, model = c.split(":", 1)
            try:
                settings.llm.resolve(provider.strip(), model.strip())
                return c
            except Exception:  # noqa: BLE001
                continue
        # 未指定 provider: 在已配置 provider 里找第一个可解析的.
        for provider in settings.llm.list_providers():
            try:
                settings.llm.resolve(provider, c)
                return f"{provider}:{c}"
            except Exception:  # noqa: BLE001
                continue
    return None
