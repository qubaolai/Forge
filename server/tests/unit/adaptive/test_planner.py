"""Planner 单元测试。"""

from __future__ import annotations

import pytest

from forge.adaptive.options import HardCaps, TaskOptions
from forge.adaptive.planner import Planner, PlannerError

pytestmark = pytest.mark.asyncio


def _options(workspace_path: str) -> TaskOptions:
    return TaskOptions(
        allow_write=True,
        allow_parallel=True,
        max_agents=4,
        writer_mode="isolated_worktree",
        verifier_cmd=None,
        workspace_path=workspace_path,
        hard_caps=HardCaps(),
    )


async def test_planner_parse_json_nodes_list(tmp_path) -> None:
    planner = Planner(
        plan_func=lambda *_: """
        {
          "nodes": [
            {
              "id": "t1",
              "title": "读取代码",
              "kind": "read",
              "allowed_tools": ["read_file"],
              "read_scope": ["server"],
              "write_scope": []
            }
          ]
        }
        """
    )
    graph = await planner.plan(
        goal="修复 bug",
        discovery_report="",
        tool_allowlist=["read_file"],
        options=_options(str(tmp_path)),
    )
    assert "t1" in graph.nodes
    assert graph.nodes["t1"].kind.value == "read"


async def test_planner_invalid_json_raises(tmp_path) -> None:
    planner = Planner(plan_func=lambda *_: "not-json")
    with pytest.raises(PlannerError, match="JSON"):
        await planner.plan(
            goal="修复 bug",
            discovery_report="",
            tool_allowlist=[],
            options=_options(str(tmp_path)),
        )


async def test_planner_fallback_graph_contains_write_when_allow_write(tmp_path) -> None:
    planner = Planner()
    graph = await planner.plan(
        goal="实现接口",
        discovery_report="",
        tool_allowlist=[],
        options=_options(str(tmp_path)),
    )
    assert any(node.kind.value == "write" for node in graph.nodes.values())
