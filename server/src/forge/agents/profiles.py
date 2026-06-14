"""AgentProfile 加载与启动期校验.

约定:
    - 启动期 (lifespan startup) 调 load_profiles_at_startup() 校验整个配置;
      任一项不通过 -> raise RuntimeError, 阻止应用启动
    - 运行期通过 get_agent_profile(mode) 取冻结后的 Profile 对象
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
    """启动期一次性加载并校验. 必须在 ToolRegistry / PromptRegistry
    都已就绪后调用.

    校验项:
        1. tools_allowed 中每个工具名在 ToolRegistry 已注册
        2. system_prompt_template 在 PromptRegistry 存在
        3. model_profile 在 model_profiles 字典定义

    任一失败 → raise RuntimeError(校验报告)
    """
    global _loaded

    # 延迟 import 防止启动期循环
    from forge.prompts import get_registry
    from forge.tools.registry import ToolRegistry

    cfg: AgentProfilesConfig = get_settings().agent_profiles
    registry = get_registry()
    profiles_cfg = cfg.profiles
    model_profile_dict = cfg.model_profiles.model_dump()
    tool_registry_names = {t.name for t in ToolRegistry.get_all()}

    errors: list[str] = []
    for mode, p in profiles_cfg.items():
        prefix = f"agent_profiles.profiles[{mode!r}]"

        # 1
        for tn in p.tools_allowed:
            if tn not in tool_registry_names:
                errors.append(f"{prefix}.tools_allowed: 未注册工具 {tn!r}")

        # 2
        if not registry.exists(p.system_prompt_template):
            errors.append(
                f"{prefix}.system_prompt_template: 模板 "
                f"{p.system_prompt_template!r} 不存在"
            )

        # 3
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


def reset_profiles() -> None:
    """测试隔离用. 清空已加载的 profiles."""
    global _loaded
    with _lock:
        _profiles.clear()
        _loaded = False


__all__ = [
    "get_agent_profile",
    "load_profiles_at_startup",
    "reset_profiles",
]
