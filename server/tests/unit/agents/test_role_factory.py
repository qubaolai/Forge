from __future__ import annotations

from pathlib import Path

from forge.agents.roles import AGENT_ROLES
from forge.prompts import get_registry, reset_registry


def test_role_prompts_render_with_user_slot(monkeypatch):
    prompts_dir = Path(__file__).resolve().parents[3] / "prompts"
    monkeypatch.setenv("PROMPTS_DIR", str(prompts_dir))
    reset_registry()
    registry = get_registry()

    required = {"triage", "developer", "architect", "reviewer", "qa", "ra", "devops"}
    assert required.issubset(set(AGENT_ROLES.keys()))

    for role_name, role in AGENT_ROLES.items():
        rendered = registry.render(
            role.prompt_template,
            user_system_prompt=f"user_slot_for_{role_name}",
            workflow_id="wf_demo",
            template_id="quick_fix",
            phase_id="phase_1",
            phase_index=0,
            phase_task="do task",
            input_message="hello",
            completed_artifacts="(none)",
            role_artifacts="(none)",
            subagent_channel_notice="",
        )
        assert f"user_slot_for_{role_name}" in rendered
        assert role_name in rendered.lower()

    # 渲染缺变量时应走兜底文本, 不抛异常.
    fallback = registry.render("roles/triage")
    assert isinstance(fallback, str)
    assert fallback


def test_role_prompts_render_with_empty_subagent_channel(monkeypatch):
    """父 agent 渲染 (subagent_channel_notice='') 不应出现子 agent 信道说明."""
    prompts_dir = Path(__file__).resolve().parents[3] / "prompts"
    monkeypatch.setenv("PROMPTS_DIR", str(prompts_dir))
    reset_registry()
    registry = get_registry()

    for role in AGENT_ROLES.values():
        rendered = registry.render(
            role.prompt_template,
            user_system_prompt="",
            workflow_id="wf",
            template_id="t",
            phase_id="p",
            phase_index=0,
            phase_task="task",
            input_message="msg",
            completed_artifacts="(none)",
            role_artifacts="(none)",
            subagent_channel_notice="",
        )
        # 父 agent 路径渲染: 不应见到子 agent 信道说明.
        assert "子 Agent 通信约束" not in rendered
        assert "ToolNotFound" not in rendered


def test_role_prompts_inject_subagent_channel_notice(monkeypatch):
    """子 agent 渲染时, system prompt 含信道说明的关键句."""
    from forge.tools.builtin.agent.spawn import SUBAGENT_CHANNEL_NOTICE

    prompts_dir = Path(__file__).resolve().parents[3] / "prompts"
    monkeypatch.setenv("PROMPTS_DIR", str(prompts_dir))
    reset_registry()
    registry = get_registry()

    for role in AGENT_ROLES.values():
        rendered = registry.render(
            role.prompt_template,
            user_system_prompt="",
            workflow_id="subagent",
            template_id="subagent",
            phase_id="subagent",
            phase_index=0,
            phase_task="task",
            input_message="msg",
            completed_artifacts="(none)",
            role_artifacts="(none)",
            subagent_channel_notice=SUBAGENT_CHANNEL_NOTICE,
        )
        # 子 agent 路径渲染: 必须见到通信约束关键短语.
        assert "只能通过返回字符串与父通信" in rendered
        assert "不能写文件" in rendered
