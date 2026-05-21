"""spawn_subagent tool.

S6.5 M2 起加入工具白名单 hard mask 与通信信道声明:
- 子 agent 在工具层物理无法改共享空间 (写类工具 / artifact 创建 / 二级派发).
- 子 agent system prompt 内显式声明只能通过返回字符串与父通信.
共享/私有边界由代码而不是 prompt 约定保证.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from forge.core.types.errors import ToolValidationError
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry, register_tool

from .context import SUBAGENT_DEPTH

SubagentRunner = Callable[[str, str, int], Awaitable[str]]
SUBAGENT_RUNNER: ContextVar[SubagentRunner | None] = ContextVar(
    "SUBAGENT_RUNNER",
    default=None,
)

# 子 agent 工具白名单 hard mask: 父调度, 子做苦力, 子只能读共享 + 返回字符串.
# 写类 / 创建类 / 二级派发类一律剥掉, 即使父 role 的 allowed_tools 含这些.
SUBAGENT_DENY_TOOLS: frozenset[str] = frozenset(
    {
        # 写文件 / 改文件 / 执行 shell — 不允许子 agent 改 workspace.
        "write_file",
        "edit_file",
        "shell",
        # 共享空间 (artifact) 创建权限只属于父; 子只能 get/search.
        "create_artifact",
        # 二级派发会让子 agent 控制流程, 与"父调度"模型冲突.
        "delegate_to_agent",
        "spawn_subagent",
    }
)

# 子 agent system prompt 末尾追加的固定信道声明.
# 父 agent (workflow phase 内) 渲染时该变量为空串, 不影响主链路.
SUBAGENT_CHANNEL_NOTICE: str = (
    "## 子 Agent 通信约束\n"
    "你是被父 agent 隔离上下文 spawn 出来的子 agent. 必须遵守:\n"
    "1. 只能通过返回字符串与父通信; 父收到字符串后自行决定要不要落 artifact.\n"
    "2. 不能写文件 / 改文件 / 执行 shell / 创建 artifact / 再派发子 agent — "
    "相关工具已在工具层被剥离, 调用会报 ToolNotFound.\n"
    "3. 需要上游产出时只读: 用 get_artifact / search_artifact / read_file / grep.\n"
    "4. 完成任务请直接给出最终文本结果, 不需要 ack / 客套."
)


@dataclass(frozen=True)
class _AgentSnapshot:
    model_id: str | None = None


@register_tool
class SpawnSubagent(Tool):
    name = "spawn_subagent"
    description = "隔离上下文运行一个子 agent, 将结果以字符串返回给当前 agent."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target_role": {"type": "string", "description": "目标 agent role"},
            "task": {"type": "string", "description": "子 agent 要完成的任务"},
            "max_steps": {"type": "integer", "description": "子 agent 最大步数"},
            "max_depth": {"type": "integer", "description": "子 agent 嵌套深度上限, 默认 2"},
        },
        "required": ["target_role", "task"],
    }
    required_scope = "workspace"

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        target_role = str(args.get("target_role") or "").strip().lower()
        task = str(args.get("task") or "").strip()
        if not target_role:
            raise ToolValidationError(self.name, "缺少 target_role")
        if not task:
            raise ToolValidationError(self.name, "缺少 task")

        from forge.agents.roles import get_agent_role

        role = get_agent_role(target_role)
        max_steps = int(args.get("max_steps") or role.max_steps)
        max_depth = int(args.get("max_depth") or 2)
        depth = SUBAGENT_DEPTH.get()
        if depth >= max_depth:
            raise ValueError("subagent 嵌套超限")

        token = SUBAGENT_DEPTH.set(depth + 1)
        try:
            runner = SUBAGENT_RUNNER.get() or _default_subagent_runner
            output = await runner(target_role, task, max_steps)
        finally:
            SUBAGENT_DEPTH.reset(token)

        return {
            "ok": True,
            "target_role": target_role,
            "output": output,
        }


def resolve_subagent_tools(role_allowed_tools: tuple[str, ...]) -> list[Tool]:
    """按 SUBAGENT_DENY_TOOLS 剥掉写/创建/派发类工具后, 解析为 Tool 实例列表.

    剥离发生在 ReActAgent 装配前, 子 agent 物理拿不到这些工具, 不依赖 prompt 自律.
    """
    return [
        tool
        for name in role_allowed_tools
        if name not in SUBAGENT_DENY_TOOLS and (tool := ToolRegistry.get(name)) is not None
    ]


async def _default_subagent_runner(target_role: str, task: str, max_steps: int) -> str:
    from config.settings import get_settings

    from forge.agents.react.agent import ReActAgent
    from forge.agents.roles import get_agent_role, resolve_runtime_model_id
    from forge.chat.llm_selection import build_llm_chain_for_agent
    from forge.prompts import get_registry

    role = get_agent_role(target_role)
    llm = build_llm_chain_for_agent(
        cast(Any, _AgentSnapshot(model_id=resolve_runtime_model_id(role))),
        get_settings(),
    )
    tools = resolve_subagent_tools(role.allowed_tools)
    system_prompt = get_registry().render(
        role.prompt_template,
        user_system_prompt="",
        workflow_id="subagent",
        template_id="subagent",
        phase_index=0,
        phase_id="subagent",
        phase_role=role.name,
        phase_task=task,
        input_message=task,
        completed_artifacts="(none)",
        role_artifacts="(none)",
        subagent_channel_notice=SUBAGENT_CHANNEL_NOTICE,
    )
    agent = ReActAgent(
        llm,
        tools=tools,
        system_prompt=system_prompt,
        max_steps=max_steps,
        role=role.name,
    )
    # 走 async stream (chat 主路径同款), 不再依赖 CLAUDE.md 标注的 "生产死代码" run().
    pieces: list[str] = []
    async for event in agent.stream(task):
        payload = event.to_dict()
        if payload.get("type") == "delta":
            pieces.append(str(payload.get("content") or ""))
    return "".join(pieces)
