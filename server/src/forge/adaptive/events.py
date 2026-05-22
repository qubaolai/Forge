"""事件类型常量 — AdaptiveRun SSE 事件。"""

RUN_CREATED = "run.created"        # API 建档时发，仅一次
RUN_STARTED = "run.started"        # supervisor 真正调起 orchestrator 时发（C3/fix-7 拆分）
RUN_STATUS_CHANGED = "run.status_changed"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_BLOCKED = "run.blocked"
RUN_ABORTED = "run.aborted"

TASK_STARTED = "task.started"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_SKIPPED = "task.skipped"

WAVE_STARTED = "wave.started"
WAVE_COMPLETED = "wave.completed"

ARTIFACT_CREATED = "artifact.created"

PLAN_CREATED = "plan.created"
PLAN_VALIDATED = "plan.validated"
PLAN_REJECTED = "plan.rejected"

INTEGRATION_STARTED = "integration.started"
INTEGRATION_COMPLETED = "integration.completed"
INTEGRATION_CONFLICT = "integration.conflict"

VERIFY_STARTED = "verify.started"
VERIFY_PASSED = "verify.passed"
VERIFY_FAILED = "verify.failed"
