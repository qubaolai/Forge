"""事件类型常量 — AdaptiveRun SSE 事件。"""

RUN_CREATED = "run.created"
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
