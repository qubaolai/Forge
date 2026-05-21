from __future__ import annotations

import pytest

import forge.tools.builtin  # noqa: F401
from forge.agents.roles import AGENT_ROLES
from forge.orchestration.workflow.models import WorkflowPhaseState
from forge.tools.builtin.agent.context import SUBAGENT_DEPTH, workflow_tool_context
from forge.tools.builtin.agent.spawn import (
    SUBAGENT_CHANNEL_NOTICE,
    SUBAGENT_DENY_TOOLS,
    SUBAGENT_RUNNER,
    resolve_subagent_tools,
)
from forge.tools.registry import ToolRegistry


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def enqueue_phase(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return WorkflowPhaseState(
            id="phase_delegated",
            role=kwargs["role"],
            task=kwargs["task"],
            input_artifact_ids=list(kwargs.get("input_artifacts") or []),
        )


@pytest.mark.asyncio
async def test_delegate_to_agent_enqueues_phase_and_exits():
    tool = ToolRegistry.get("delegate_to_agent")
    assert tool is not None
    orchestrator = _FakeOrchestrator()

    with workflow_tool_context(orchestrator, "wf_delegate"):
        result = await tool.arun(
            {
                "target_role": "developer",
                "task": "implement patch",
                "input_artifacts": ["art_1"],
            }
        )

    assert result["status"] == "delegated"
    assert result["phase_id"] == "phase_delegated"
    assert orchestrator.calls == [
        {
            "workflow_id": "wf_delegate",
            "role": "developer",
            "task": "implement patch",
            "input_artifacts": ["art_1"],
        }
    ]


@pytest.mark.asyncio
async def test_spawn_subagent_isolates_context():
    tool = ToolRegistry.get("spawn_subagent")
    assert tool is not None
    calls: list[tuple[str, str, int, int]] = []

    async def runner(role: str, task: str, max_steps: int) -> str:
        calls.append((role, task, max_steps, SUBAGENT_DEPTH.get()))
        return "subagent result"

    token = SUBAGENT_RUNNER.set(runner)
    try:
        result = await tool.arun(
            {
                "target_role": "developer",
                "task": "inspect file",
                "max_steps": 4,
            }
        )
    finally:
        SUBAGENT_RUNNER.reset(token)

    assert result["output"] == "subagent result"
    assert calls == [("developer", "inspect file", 4, 1)]
    assert SUBAGENT_DEPTH.get() == 0


def test_subagent_deny_tools_contains_write_class():
    """SUBAGENT_DENY_TOOLS 至少包含写类 / 创建 / 二级派发类的硬约束工具集."""
    required = {
        "write_file",
        "edit_file",
        "shell",
        "create_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    }
    assert required.issubset(SUBAGENT_DENY_TOOLS)


def test_resolve_subagent_tools_strips_write_class_tools():
    """developer role 的工具集经 hard mask 后, write_file/edit_file/shell 全部消失."""
    developer = AGENT_ROLES["developer"]
    assert "write_file" in developer.allowed_tools  # 父 role 本身有这些工具
    assert "edit_file" in developer.allowed_tools
    assert "shell" in developer.allowed_tools

    tool_names = {t.name for t in resolve_subagent_tools(developer.allowed_tools)}
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "shell" not in tool_names


def test_resolve_subagent_tools_strips_create_artifact_keeps_read_class():
    """create_artifact 剥, 但 get_artifact / search_artifact (读) 保留."""
    developer = AGENT_ROLES["developer"]
    tool_names = {t.name for t in resolve_subagent_tools(developer.allowed_tools)}
    assert "create_artifact" not in tool_names
    assert "get_artifact" in tool_names
    assert "search_artifact" in tool_names


def test_resolve_subagent_tools_strips_delegate_and_spawn():
    """子 agent 不能再调度子孙 agent: delegate / spawn 都被剥."""
    architect = AGENT_ROLES["architect"]
    tool_names = {t.name for t in resolve_subagent_tools(architect.allowed_tools)}
    assert "delegate_to_agent" not in tool_names
    assert "spawn_subagent" not in tool_names


def test_subagent_channel_notice_constant_has_required_phrases():
    """SUBAGENT_CHANNEL_NOTICE 内容包含强约束关键短语, 用于反向证明信道说明被注入."""
    assert "只能通过返回字符串与父通信" in SUBAGENT_CHANNEL_NOTICE
    assert "不能写文件" in SUBAGENT_CHANNEL_NOTICE
    assert "ToolNotFound" in SUBAGENT_CHANNEL_NOTICE


@pytest.mark.asyncio
async def test_spawn_subagent_max_depth_2():
    tool = ToolRegistry.get("spawn_subagent")
    assert tool is not None

    depth_token = SUBAGENT_DEPTH.set(2)
    try:
        with pytest.raises(ValueError, match="嵌套超限"):
            await tool.arun(
                {
                    "target_role": "developer",
                    "task": "too deep",
                    "max_depth": 2,
                }
            )
    finally:
        SUBAGENT_DEPTH.reset(depth_token)
