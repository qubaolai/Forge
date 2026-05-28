"""WorkflowLifecycle: 模板驱动的多 phase 流水线 (CLI 工作流模式).

模板形态 (POST /v1/runs body.workflow_template):
    {
        "name": "code_review_workflow",
        "description": "代码评审三步流程",
        "phases": [
            {"id": "analyze", "role": "architect", "task": "分析代码结构"},
            {"id": "review",  "role": "reviewer", "task": "代码评审"},
            {"id": "report",  "role": "developer", "task": "汇总报告"}
        ],
        "gates": [
            {"after_phase": "review"}    # review 完成后暂停, 等用户决定继不继续
        ]
    }

机制 (与 PlanModeLifecycle 同款 HITL):
    1. LLM 按 system prompt 严格 phase 顺序执行 (spawn_subagent + create_artifact)
    2. 每完成一个 phase, 调 `advance_phase` 工具 (新增, 仅作 marker)
    3. lifecycle.before_tool_call 拦截 advance_phase:
        - 校验当前 phase id 与模板匹配
        - 若该 phase 有 gate → DecisionRegistry.create(kind="workflow_gate") → await
        - 用户批准 → 继续到下一 phase
        - 用户拒绝 → 把 feedback 回灌给 LLM, agent 自行决定后续
    4. 全部 phase 完成 → advance_phase 返回 "workflow_completed", LLM 应输出综合报告

不直接 emit SSE 事件, 通过 RunStorePersistenceLifecycle 自然落 events.jsonl;
gate 阻塞期间 events.jsonl 含 workflow_gate_required 事件 (lifecycle 写).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from forge.agents.hitl import (
    Decision,
    DecisionRegistry,
    get_decision_registry,
)
from forge.agents.lifecycle import RunContext, StepContext, ToolCallVeto
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.run_store import RunStore

logger = logging.getLogger(__name__)


class WorkflowTemplateError(ValueError):
    """模板格式错误."""


def validate_workflow_template(tpl: dict[str, Any]) -> None:
    """校验模板基础形状. 不通过 -> raise WorkflowTemplateError."""
    if not isinstance(tpl, dict):
        raise WorkflowTemplateError("workflow_template 必须是 dict")
    phases = tpl.get("phases")
    if not isinstance(phases, list) or not phases:
        raise WorkflowTemplateError("workflow_template.phases 必须是非空列表")
    seen_ids: set[str] = set()
    for i, ph in enumerate(phases):
        if not isinstance(ph, dict):
            raise WorkflowTemplateError(f"phases[{i}] 必须是 dict")
        pid = ph.get("id")
        if not pid or not isinstance(pid, str):
            raise WorkflowTemplateError(f"phases[{i}].id 必填且必须是 str")
        if pid in seen_ids:
            raise WorkflowTemplateError(f"phases[{i}].id={pid!r} 重复")
        seen_ids.add(pid)
        if not ph.get("role"):
            raise WorkflowTemplateError(f"phases[{i}].role 必填")
        if not ph.get("task"):
            raise WorkflowTemplateError(f"phases[{i}].task 必填")
    for i, g in enumerate(tpl.get("gates") or []):
        if not isinstance(g, dict):
            raise WorkflowTemplateError(f"gates[{i}] 必须是 dict")
        after = g.get("after_phase")
        if after not in seen_ids:
            raise WorkflowTemplateError(
                f"gates[{i}].after_phase={after!r} 不在 phases 中"
            )


class WorkflowLifecycle:
    """Workflow 模式主 lifecycle.

    Args:
        template:   已通过 validate_workflow_template 校验的模板 dict
        run_id:     关联的 RunRecord.run_id
        store:      RunStore (用于落 phase_started / gate_required 事件)
        registry:   DecisionRegistry, 默认走全局单例
        ttl_sec:    workflow_gate 决策超时 (默认 30 分钟)
    """

    def __init__(
        self,
        template: dict[str, Any],
        *,
        run_id: str,
        owner_user_id: str | None = None,
        store: RunStore | None = None,
        registry: DecisionRegistry | None = None,
        ttl_sec: int = 1800,
    ) -> None:
        validate_workflow_template(template)
        self._template = template
        self._run_id = run_id
        self._owner_user_id = owner_user_id
        self._store = store
        self._registry = registry or get_decision_registry()
        self._ttl_sec = ttl_sec
        self._phases: list[dict] = list(template.get("phases") or [])
        self._gates_by_phase: dict[str, dict] = {
            g["after_phase"]: g for g in (template.get("gates") or [])
        }
        # 当前 phase 索引 (0 = 第一 phase 进行中); 完成全部时 = len(phases)
        self._current_idx = 0

    @property
    def current_phase(self) -> dict | None:
        if self._current_idx >= len(self._phases):
            return None
        return self._phases[self._current_idx]

    async def on_start(self, ctx: RunContext) -> None:
        if self._owner_user_id is None:
            self._owner_user_id = ctx.user_id
        if self._store is not None:
            try:
                await self._store.append_event(
                    self._run_id,
                    "workflow_started",
                    {
                        "name": self._template.get("name", ""),
                        "phases_count": len(self._phases),
                        "gates_count": len(self._gates_by_phase),
                    },
                )
                if self.current_phase is not None:
                    await self._store.append_event(
                        self._run_id,
                        "phase_started",
                        {
                            "phase_index": 0,
                            "phase_id": self.current_phase["id"],
                            "role": self.current_phase["role"],
                        },
                    )
            except Exception:  # noqa: BLE001
                logger.exception("workflow on_start 写事件失败 run=%s", self._run_id)

    async def before_tool_call(
        self, tc: ToolCall, step: StepContext
    ) -> ToolCallVeto | None:
        if tc.name != "advance_phase":
            return None

        completed_id = str(tc.arguments.get("phase_id") or "").strip()
        cur = self.current_phase
        if cur is None:
            return self._veto(tc, "workflow_already_completed", {
                "approved": False,
                "feedback": "workflow 已全部完成, 无需再 advance_phase",
            })
        if completed_id and completed_id != cur["id"]:
            return self._veto(tc, "phase_mismatch", {
                "approved": False,
                "feedback": (
                    f"当前应完成的 phase 是 {cur['id']!r}, 但 advance_phase 传入 {completed_id!r}; "
                    f"请按模板顺序执行"
                ),
            })

        completed_phase = cur
        # 写 phase_completed 事件
        if self._store is not None:
            try:
                await self._store.append_event(
                    self._run_id,
                    "phase_completed",
                    {
                        "phase_index": self._current_idx,
                        "phase_id": completed_phase["id"],
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "workflow phase_completed 写事件失败 run=%s phase=%s",
                    self._run_id, completed_phase["id"],
                )

        # 检测 gate
        gate = self._gates_by_phase.get(completed_phase["id"])
        if gate is not None:
            decided = await self._await_gate(completed_phase, gate)
            if decided is None or not decided.approved:
                # 拒绝 = 终止 workflow (LLM 可选择性输出错误说明)
                feedback = (
                    decided.feedback if decided and decided.feedback
                    else "用户在 workflow_gate 处拒绝继续, workflow 终止"
                )
                return self._veto(tc, "workflow_gate_rejected", {
                    "approved": False,
                    "feedback": feedback,
                    "current_phase": completed_phase["id"],
                    "next": "workflow 被中止. 请输出已完成 phase 的综合说明, 然后结束.",
                })

        # 推进到下一 phase
        self._current_idx += 1
        next_phase = self.current_phase
        if next_phase is not None and self._store is not None:
            try:
                await self._store.append_event(
                    self._run_id,
                    "phase_started",
                    {
                        "phase_index": self._current_idx,
                        "phase_id": next_phase["id"],
                        "role": next_phase["role"],
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "workflow phase_started 写事件失败 run=%s phase=%s",
                    self._run_id, next_phase["id"],
                )

        # 给 LLM 的回包 = 当前进度 + 下一步任务
        if next_phase is None:
            return self._veto(tc, "workflow_completed", {
                "approved": True,
                "completed_phase": completed_phase["id"],
                "next": "全部 phase 已完成. 请输出综合执行报告, 结束 workflow.",
            })
        return self._veto(tc, "phase_advanced", {
            "approved": True,
            "completed_phase": completed_phase["id"],
            "next_phase": next_phase["id"],
            "next_phase_role": next_phase["role"],
            "next_phase_task": next_phase["task"],
            "remaining_phases": [
                p["id"] for p in self._phases[self._current_idx:]
            ],
        })

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    async def _await_gate(
        self, completed_phase: dict, gate: dict
    ) -> Decision | None:
        pending = self._registry.create(
            kind="workflow_gate",
            payload={
                "completed_phase": completed_phase["id"],
                "gate": gate,
                "remaining_phases": [
                    p["id"] for p in self._phases[self._current_idx + 1:]
                ],
            },
            run_id=self._run_id,
            owner_user_id=self._owner_user_id,
            ttl_sec=self._ttl_sec,
        )
        if self._store is not None:
            try:
                await self._store.transition_status(
                    self._run_id,
                    "blocked",
                    payload={
                        "reason": "workflow_gate_required",
                        "token": pending.token,
                        "completed_phase": completed_phase["id"],
                    },
                )
                await self._store.append_event(
                    self._run_id,
                    "workflow_gate_required",
                    {
                        "token": pending.token,
                        "completed_phase": completed_phase["id"],
                        "ttl_sec": self._ttl_sec,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "workflow_gate_required 写事件失败 run=%s phase=%s",
                    self._run_id, completed_phase["id"],
                )
        logger.info(
            "Workflow gate 等待: token=%s run=%s phase=%s",
            pending.token, self._run_id, completed_phase["id"],
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(
                pending.event.wait(), timeout=self._ttl_sec + 5,
            )
        decided = pending.decided
        self._registry.remove(pending.token)
        if self._store is not None:
            try:
                await self._store.transition_status(
                    self._run_id,
                    "running",
                    payload={
                        "reason": "workflow_gate_received",
                        "token": pending.token,
                        "approved": bool(decided and decided.approved),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("workflow gate 恢复 running 失败 run=%s", self._run_id)
        return decided

    def _veto(
        self, tc: ToolCall, reason: str, payload: dict
    ) -> ToolCallVeto:
        content = json.dumps(payload, ensure_ascii=False)
        return ToolCallVeto(
            blocked=True,
            reason=reason,
            replacement_message=Message(
                role="tool",
                tool_call_id=tc.id,
                name=tc.name,
                content=content,
            ),
        )


__all__ = [
    "WorkflowLifecycle",
    "WorkflowTemplateError",
    "validate_workflow_template",
]
