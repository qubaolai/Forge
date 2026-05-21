"""角色注册表导出."""

from .factory import (
    AGENT_ROLES,
    AgentRole,
    get_agent_role,
    list_agent_roles,
    register_custom_agent_role,
    reset_custom_agent_roles,
    resolve_runtime_model_id,
)

__all__ = [
    "AGENT_ROLES",
    "AgentRole",
    "get_agent_role",
    "list_agent_roles",
    "register_custom_agent_role",
    "reset_custom_agent_roles",
    "resolve_runtime_model_id",
]
