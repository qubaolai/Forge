"""PlannerLLM — 用 LLM tool_use 输出结构化 TaskGraph JSON (B8/P0-2 下半).

设计要点:
- 用 ``chat_with_tools`` + 一个虚拟工具 ``submit_task_graph`` 强制 LLM 以结构化
  JSON 返回 TaskGraph，避免自由文本里再 parse。
- 与 ``Planner._parse_task_graph`` 契约对齐：输出 ``{"nodes": [...]}``。
- 任何异常 / 空 tool_call 返回 ``""``，由 ``Planner._fallback_plan`` 兜底，
  保证 LLM 不可用时整条链路仍能跑通。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from forge.adaptive.options import TaskOptions
from forge.adaptive.planner import PlannerError

logger = logging.getLogger(__name__)

PlannerCallable = Callable[[str, str, list[str], TaskOptions], Awaitable[str]]


SUBMIT_TASK_GRAPH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_task_graph",
        "description": (
            "提交一份完整的 TaskGraph。每个 TaskNode 包含 id/title/kind/allowed_tools/"
            "read_scope/write_scope/deps/max_steps/output_contract 等字段。"
            "整图必须是无环 DAG，节点 id 全局唯一。"
        ),
        "parameters": {
            "type": "object",
            "required": ["nodes"],
            "properties": {
                "nodes": {
                    "type": "array",
                    "description": "TaskNode 列表",
                    "items": {
                        "type": "object",
                        "required": ["id", "title", "kind", "allowed_tools"],
                        "properties": {
                            "id": {"type": "string", "description": "任务唯一 id，如 t1 / t2"},
                            "title": {"type": "string", "description": "任务标题（中文一句话）"},
                            "kind": {
                                "type": "string",
                                "enum": ["read", "write", "execute", "review", "integrate"],
                                "description": "任务类型。READ 无副作用；WRITE 产出 PatchSet；"
                                "EXECUTE 跑命令；REVIEW 读判；INTEGRATE 合并多产出。",
                            },
                            "allowed_tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Validator 会强制校验为 tool_allowlist 子集。",
                            },
                            "read_scope": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "允许读取的路径前缀（相对 workspace）",
                            },
                            "write_scope": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "允许写入的路径前缀；READ/EXECUTE 必须为空",
                            },
                            "deps": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "依赖任务 id 列表",
                            },
                            "model_profile": {
                                "type": "string",
                                "enum": ["fast", "smart", "strong"],
                                "description": "执行该任务的 LLM 档位，默认 smart",
                            },
                            "max_steps": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "ReAct 最大步数，受 hard_caps 上限",
                            },
                            "acceptance_criteria": {"type": "string"},
                            "output_contract": {
                                "type": "string",
                                "enum": [
                                    "patch_set",
                                    "discovery_report",
                                    "review_report",
                                    "test_report",
                                    "integration_report",
                                    "final_report",
                                ],
                            },
                            "command": {
                                "type": "string",
                                "description": "仅 EXECUTE 任务可声明的 shell 命令",
                            },
                        },
                    },
                }
            },
        },
    },
}


def _build_planner_system_prompt(tool_allowlist: list[str], options: TaskOptions) -> str:
    """B15: 优先用 prompts/adaptive/planner.j2，失败回退内联。"""
    try:
        from forge.prompts import get_registry

        registry = get_registry()
        if registry.exists("adaptive/planner"):
            return registry.render(
                "adaptive/planner",
                tool_allowlist=tool_allowlist,
                allow_write=options.allow_write,
                max_steps_per_task=options.hard_caps.max_steps_per_task,
            )
    except Exception:
        logger.exception("Planner prompt 模板渲染失败，使用 fallback")

    allow_write_note = (
        "allow_write=True：可生成 WRITE / INTEGRATE 节点"
        if options.allow_write
        else "allow_write=False：禁止任何 WRITE / INTEGRATE 节点和写工具"
    )
    return (
        "你是 Forge adaptive run 的 Planner。\n"
        "基于 user 给出的 goal 和 discovery_report，输出一份完整 TaskGraph，"
        "必须通过 submit_task_graph 工具调用返回，不要直接写自由文本。\n\n"
        f"## 工作区策略\n- {allow_write_note}\n"
        f"- 工具白名单（节点 allowed_tools 必须是其子集）: {tool_allowlist}\n"
        f"- 每个任务 max_steps <= {options.hard_caps.max_steps_per_task}\n\n"
        "## 设计约束\n"
        "- TaskGraph 必须是无环 DAG，依赖之间 wave 拆分由 scheduler 完成；\n"
        "- WRITE 任务必须声明 write_scope（仅工作区子路径）；\n"
        "- READ 任务不允许 write_scope 与写工具；\n"
        "- EXECUTE 任务不允许 write_scope，可声明 command；\n"
        "- INTEGRATE 任务必须依赖至少一个其他任务且声明 write_scope。\n"
        "- 不要重复 Discovery 节点（系统已经做过 DiscoveryReport）。"
    )


def build_real_planner_callable(
    *,
    model_profile: str = "smart",
) -> PlannerCallable:
    """构造一个真实接入 LLM 的 PlannerCallable。

    返回的可调用对象签名: ``async (goal, discovery_report, tool_allowlist, options) -> str``。
    返回 JSON 字符串供 ``Planner._parse_task_graph`` 解析；失败返回 ``""``。
    """

    async def _plan(
        goal: str,
        discovery_report: str,
        tool_allowlist: list[str],
        options: TaskOptions,
    ) -> str:
        import asyncio

        from config.settings import get_settings

        from forge.llm.gateway import build_chain_from_settings

        # C10/D1: 真实路径下 LLM 不可用必须让 run FAILED，不再回退 fallback 假完成
        try:
            settings = get_settings()
            target_model = _resolve_model_profile(settings, model_profile)
            chain = build_chain_from_settings(settings, model=target_model)
        except Exception as exc:
            logger.exception("Planner LLM chain 构造失败")
            raise PlannerError(f"Planner LLM chain 构造失败: {exc}") from exc

        # C2/fix-2a: Message 实际定义在 core.types.message，
        # 之前 `from forge.llm.streaming import Message` 是错误的（streaming 是 placeholder）
        from forge.core.types.message import Message

        messages = [
            Message(
                role="system",
                content=_build_planner_system_prompt(tool_allowlist, options),
            ),
            Message(
                role="user",
                content=(
                    f"## 目标\n{goal}\n\n"
                    f"## DiscoveryReport\n{discovery_report or '(无)'}\n\n"
                    "请调用 submit_task_graph 输出 TaskGraph。"
                ),
            ),
        ]

        try:
            resp = await asyncio.to_thread(
                chain.chat_with_tools,
                messages,
                [SUBMIT_TASK_GRAPH_TOOL],
                tool_choice="auto",
            )
        except Exception as exc:
            logger.exception("Planner LLM 调用失败")
            raise PlannerError(f"Planner LLM 调用失败: {exc}") from exc

        tool_calls = resp.get("tool_calls") or []
        if not tool_calls:
            raise PlannerError("Planner LLM 未返回 tool_calls（submit_task_graph）")
        # 取第一个 submit_task_graph 调用
        for tc in tool_calls:
            tc_name = getattr(tc, "name", None) or (
                tc.get("name") if isinstance(tc, dict) else None
            )
            if tc_name != "submit_task_graph":
                continue
            tc_args = getattr(tc, "arguments", None)
            if tc_args is None and isinstance(tc, dict):
                tc_args = tc.get("arguments")
            if isinstance(tc_args, dict):
                return json.dumps(tc_args, ensure_ascii=False)
            if isinstance(tc_args, str):
                return tc_args
        raise PlannerError("Planner LLM 返回的 tool_calls 没有 submit_task_graph")

    return _plan


def _resolve_model_profile(settings, model_profile: str) -> str | None:
    """把 fast/smart/strong 档位解析成具体模型名。"""
    profiles = getattr(getattr(settings, "task_execution", None), "model_profiles", None)
    if profiles is None:
        return None
    if isinstance(profiles, dict):
        value = profiles.get(model_profile)
    else:
        value = getattr(profiles, model_profile, None)
    return str(value) if value else None
