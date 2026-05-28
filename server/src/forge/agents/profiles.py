"""AgentProfile 加载与启动期校验.

约定:
    - 启动期 (lifespan startup) 调 load_profiles_at_startup() 校验整个配置;
      任一项不通过 -> raise RuntimeError, 阻止应用启动
    - 运行期通过 get_agent_profile(mode) 取冻结后的 Profile 对象
    - 模式列表通过 list_modes() 暴露给 API (如 GET /v1/agent_modes)
"""

from __future__ import annotations

import logging
import threading

from forge.config.domains.agent_profiles import AgentProfile, AgentProfilesConfig
from forge.config.settings import get_settings

logger = logging.getLogger(__name__)


_lock = threading.Lock()
_profiles: dict[str, AgentProfile] = {}
_loaded = False


def load_profiles_at_startup() -> dict[str, AgentProfile]:
    """启动期一次性加载并校验. 必须在 ToolRegistry / AGENT_ROLES / PromptRegistry
    都已就绪后调用.

    校验项:
        1. tools_allowed / readonly_tools 中每个工具名在 ToolRegistry 已注册
        2. readonly_tools ⊆ tools_allowed
        3. sub_agents_allowed 中每个 role 在 agents/roles 已注册
        4. plan_mode_initial=True ⇒ exit_plan_mode in readonly_tools
        5. sub_agents_allowed 非空 ⇔ spawn_subagent in tools_allowed
        6. system_prompt_template 在 PromptRegistry 存在
        7. model_profile 在 model_profiles 字典定义

    任一失败 → raise RuntimeError(校验报告)
    """
    global _loaded

    # 延迟 import 防止启动期循环
    from forge.agents.roles import AGENT_ROLES
    from forge.prompts import get_registry
    from forge.tools.registry import ToolRegistry

    cfg: AgentProfilesConfig = get_settings().agent_profiles
    registry = get_registry()
    profiles_cfg = cfg.profiles
    model_profile_dict = cfg.model_profiles.model_dump()
    tool_registry_names = {t.name for t in ToolRegistry.get_all()}
    role_names = set(AGENT_ROLES.keys())

    errors: list[str] = []
    for mode, p in profiles_cfg.items():
        prefix = f"agent_profiles.profiles[{mode!r}]"

        # 1 + 2
        tool_set = set(p.tools_allowed)
        for tn in p.tools_allowed:
            if tn not in tool_registry_names:
                errors.append(f"{prefix}.tools_allowed: 未注册工具 {tn!r}")
        for tn in p.readonly_tools:
            if tn not in tool_registry_names:
                errors.append(f"{prefix}.readonly_tools: 未注册工具 {tn!r}")
            if tn not in tool_set:
                errors.append(
                    f"{prefix}.readonly_tools: {tn!r} 不在 tools_allowed 中"
                )

        # 3
        for role in p.sub_agents_allowed:
            if role not in role_names:
                errors.append(f"{prefix}.sub_agents_allowed: 未知角色 {role!r}")

        # 4
        if p.plan_mode_initial and "exit_plan_mode" not in p.readonly_tools:
            errors.append(
                f"{prefix}: plan_mode_initial=true 但 readonly_tools 缺 exit_plan_mode"
            )

        # 5
        spawn_in_tools = "spawn_subagent" in tool_set
        sub_agents_non_empty = bool(p.sub_agents_allowed)
        if spawn_in_tools != sub_agents_non_empty:
            errors.append(
                f"{prefix}: spawn_subagent 与 sub_agents_allowed 不一致 "
                f"(spawn_in_tools={spawn_in_tools}, sub_agents_non_empty={sub_agents_non_empty})"
            )

        # 6
        if not registry.exists(p.system_prompt_template):
            errors.append(
                f"{prefix}.system_prompt_template: 模板 "
                f"{p.system_prompt_template!r} 不存在"
            )

        # 7
        if not model_profile_dict.get(p.model_profile):
            errors.append(
                f"{prefix}.model_profile: 未定义档位 {p.model_profile!r}, "
                f"可选: {list(model_profile_dict.keys())}"
            )

    if errors:
        report = "agent_profiles 配置校验失败:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(report)

    with _lock:
        _profiles.clear()
        _profiles.update(profiles_cfg)
        _loaded = True

    logger.info("agent_profiles 加载成功: %s", list(_profiles.keys()))
    return dict(_profiles)


def get_agent_profile(mode: str) -> AgentProfile:
    """取冻结后的 Profile. 未加载或未知 mode 抛错."""
    if not _loaded:
        raise RuntimeError(
            "agent_profiles 未加载, 请确保 lifespan startup 调用了 load_profiles_at_startup()"
        )
    if mode not in _profiles:
        raise ValueError(
            f"未知 agent mode: {mode!r}, 可选: {sorted(_profiles.keys())}"
        )
    return _profiles[mode]


def list_modes() -> list[str]:
    """列出已加载的所有 mode (按字典序)."""
    if not _loaded:
        return []
    return sorted(_profiles.keys())


def reset_profiles() -> None:
    """测试隔离用. 清空已加载的 profiles."""
    global _loaded
    with _lock:
        _profiles.clear()
        _loaded = False


__all__ = [
    "get_agent_profile",
    "list_modes",
    "load_profiles_at_startup",
    "reset_profiles",
]
