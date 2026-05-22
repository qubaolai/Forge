"""DiscoveryAgent — 接入 ReActAgent 做 READ-only 代码库探索 (B7/P0-2 上半).

设计目标:
- orchestrator._discover() 默认仍走轻量占位（保证零依赖测试和默认体验可用）;
- 通过 ``build_real_discovery_callable()`` 显式装配一个 ReActAgent 驱动的探索器,
  由 supervisor / 生产入口注入到 orchestrator.

ReActAgent 内部强依赖 settings.llm（provider/key/model 配置），调用方需要
确保 LLM 池就绪. 单测环境若没有 mock 应避免开启真实 discovery.

Discovery 工具集:
- ``list_directory`` / ``glob_search`` / ``read_file`` / ``grep`` —— 只读
- 不放 shell / write_file / edit_file —— 严格遵守 "READ kind 无副作用" 红线
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


# 与 _discover 契约一致的可调用对象签名：
# 接收 (goal, workspace_path) 返回 summary 字符串
DiscoveryCallable = Callable[[str, str], Awaitable[str]]


# READ-only 工具白名单（与 sys_config.tool_allowlist 子集对应）
DEFAULT_DISCOVERY_TOOLS: tuple[str, ...] = (
    "list_directory",
    "glob_search",
    "read_file",
    "grep",
)


_DISCOVERY_FALLBACK = (
    "你是 Forge 适配运行（adaptive run）流程的 Discovery Agent。\n"
    "你的工作是在严格只读模式下探索目标工作区，为后续 Planner 提供有用上下文。\n"
    "工作区路径: {workspace_path}\n\n"
    "## 输出要求\n"
    "调用最多 8 步只读工具完成探索后，请用一段简洁中文返回 DiscoveryReport。\n"
    "禁止：调用任何写工具、运行 shell、提议具体补丁。"
)


def _build_discovery_prompt(workspace_path: str, max_steps: int) -> str:
    """B15: 优先用 prompts/adaptive/discovery.j2，模板不存在时降级内联文本。"""
    try:
        from forge.prompts import get_registry

        registry = get_registry()
        if registry.exists("adaptive/discovery"):
            return registry.render(
                "adaptive/discovery",
                workspace_path=workspace_path,
                max_steps=max_steps,
            )
    except Exception:
        logger.exception("Discovery prompt 模板渲染失败，使用 fallback")
    return _DISCOVERY_FALLBACK.format(workspace_path=workspace_path)


def build_real_discovery_callable(
    *,
    max_steps: int = 8,
    model_profile: str = "fast",
) -> DiscoveryCallable:
    """构造一个真实接入 ReActAgent 的 DiscoveryCallable.

    Returns:
        async 函数：(goal, workspace_path) -> summary string
    """

    async def _discover(goal: str, workspace_path: str) -> str:
        # 局部 import：避免 settings/LLM 在单测 collection 阶段被强制求值
        import asyncio

        from config.settings import get_settings

        from forge.agents.react.agent import ReActAgent
        from forge.llm.gateway import build_chain_from_settings
        from forge.tools.registry import ToolRegistry

        try:
            settings = get_settings()
        except Exception:
            logger.exception("Discovery 获取 settings 失败，降级为空报告")
            return ""

        # 选择模型 profile（fast 即可，探索任务不需要 strong）
        profiles = getattr(getattr(settings, "task_execution", None), "model_profiles", None)
        target_model: str | None = None
        if profiles is not None:
            target_model = (
                profiles.get(model_profile)
                if isinstance(profiles, dict)
                else getattr(profiles, model_profile, None)
            )
        try:
            chain = build_chain_from_settings(settings, provider=None, model=target_model)
        except Exception:
            logger.exception("Discovery LLM chain 构造失败，降级为空报告")
            return ""

        # 只取只读工具子集
        try:
            all_tools = ToolRegistry.get_all()
            allowed_names = set(DEFAULT_DISCOVERY_TOOLS)
            tools = [t for t in all_tools if t.name in allowed_names]
        except Exception:
            logger.exception("Discovery 工具集准备失败，降级为空报告")
            return ""
        if not tools:
            logger.warning("Discovery 工具集为空，降级为占位报告")
            return ""

        agent = ReActAgent(
            llm=chain,
            tools=tools,
            system_prompt=_build_discovery_prompt(workspace_path, max_steps),
            max_steps=max_steps,
            role="discovery",
        )

        user_input = (
            f"目标: {goal}\n\n"
            "请按 system 中的 DiscoveryReport 结构产出探索结果。"
        )
        try:
            # ReActAgent.run() 是同步的（内部用 to_thread 调 LLM），用 to_thread 转 async
            result = await asyncio.to_thread(agent.run, user_input)
        except Exception:
            logger.exception("Discovery Agent 执行异常，降级为空报告")
            return ""
        summary = (result.output or "").strip()
        logger.info("Discovery 完成 steps=%d summary_len=%d", result.steps, len(summary))
        return summary

    return _discover
