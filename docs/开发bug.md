# 开发bug

审查日期：2026-05-22

审查范围：按《项目开发说明.md》和 AGENTS.md，复查当前 `server/` 端 adaptive 主流程、API、配置、执行隔离、集成、验证、事件和测试状态。

## 总体结论

第二轮复查发现，上一轮记录的一部分问题已经被补丁修复或缓解：

- `/api/v1/runs` 现在会通过 `RunSupervisor` 启动后台 AdaptiveRun。
- `/runs/{id}/decide` 的 continue 会重新唤起 supervisor。
- TaskGraph artifact 已经落盘。
- `allow_write=False` 已经进入 Validator 和 Executor 的基础校验。
- SSE 传入不存在的 `after_event_id` 时已经返回 400。
- Executor 已经支持 `allow_parallel`、`max_agents`、`max_task_retries` 的基础控制。
- Integrator 已经记录 `integrated_patch_ids`，并用 `git apply --check` 做预检查。

但当前 `server/` 端仍不能判断为 M9 后“服务端已完整实现”。原因是：真实 Adaptive 执行链路默认关闭，打开后仍存在配置、导入、执行隔离和权限边界问题；默认 fallback 路径仍会生成“看似完成”的占位产物，不能保证产出可落地代码。

## P0 问题

### 1. 真实 adaptive 主链默认关闭，默认运行仍是 fallback/占位产物

涉及文件：

- `server/config/sys_config.yaml`
- `server/config/domains/task_execution.py`
- `server/src/forge/adaptive/supervisor.py`
- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/planner.py`
- `server/src/forge/adaptive/executor.py`

现状：

- `task_execution.enable_real_llm` 默认是 `false`。
- supervisor 只有在 `enable_real_llm=true` 时才装配真实 Discovery、PlannerLLM 和 TaskRunner。
- 默认路径下 Discovery、Planner、Executor 仍会回退到占位逻辑。
- fallback Executor 可以返回空 PatchSet、空 stdout/stderr、`passed=True` 等结果。

影响：

- 默认 API 路径会显示 run 完成，但并不代表真实执行了代码修改。
- M9 的 wave 并行只是调度层成立，不代表真实多 Agent 编码执行成立。

建议：

- 明确区分 `mock/fallback` 模式和 `production` 模式，API 返回中暴露 `execution_mode`。
- 生产模式不应允许 fallback 假完成；真实链路不可用时应 FAILED 或 BLOCKED。
- 对空 PatchSet 增加验收失败逻辑，避免任务“无改动完成”。

### 2. `enable_real_llm=true` 路径目前存在阻断级问题

涉及文件：

- `server/src/forge/adaptive/planner_llm.py`
- `server/src/forge/adaptive/task_runner.py`
- `server/src/forge/adaptive/discovery.py`
- `server/config/sys_config.yaml`
- `server/config/sys_config.test.yaml`
- `server/config/sys_config.dev.yaml`
- `server/src/forge/llm/streaming.py`

问题 1：

- `planner_llm.py` 中使用 `from forge.llm.streaming import Message`。
- 但 `server/src/forge/llm/streaming.py` 目前只是 placeholder，没有导出 `Message`。
- 真实 PlannerLLM 首次调用时会 ImportError。

问题 2：

- `task_execution.model_profiles` 默认是 `claude-sonnet-4-6` / `claude-opus-4-7`。
- 当前 LLM provider 配置中没有这些模型名。
- `build_chain_from_settings(settings, model=target_model)` 没有同步指定 provider，会落到默认 provider，例如 dashscope，然后触发“model 不在 provider 配置中”。

影响：

- 即使把 `enable_real_llm` 打开，真实规划和真实任务执行也大概率无法启动。
- 当前测试没有覆盖 `enable_real_llm=true` 分支，风险没有被 CI 捕获。

建议：

- `planner_llm.py` 改用 `forge.core.types.message.Message`。
- 给 `model_profiles` 增加 provider+model 结构，或实现模型别名解析。
- 增加 `enable_real_llm=true` 的最小集成测试，至少 mock LLM tool_call 到 TaskGraph。

### 3. Executor 真实路径未复用 TurnOrchestrator：更准确地说是设计变更未同步文档

涉及文件：

- `server/src/forge/adaptive/task_runner.py`
- `server/src/forge/adaptive/executor.py`
- `server/src/forge/chat/orchestrator.py`
- `项目开发说明.md`

预期：

- 文档红线要求 TurnOrchestrator 接口不变。
- adaptive executor 应通过 RunTurnOverrides 驱动 TurnOrchestrator，不修改聊天路径。

现状：

- 当前真实路径是 TaskRunner 直接实例化 `ReActAgent`。
- 没有看到 RunTurnOverrides 或等价 overrides 层。
- 因此 adaptive 路径绕开了 TurnOrchestrator 的上下文组装、消息生命周期、部分 guard/runner 逻辑。

补充判断：

- 这个实现选择有合理性：TurnOrchestrator 内置 session/message 持久化、resume、turn 级 guard 等聊天语义；adaptive run 的任务执行语义更接近一次性 artifact 产出，强行复用会污染 chat 路径并引入大量条件分支。
- 因此它不一定是代码 bug，但它是当前文档红线与实现架构不一致。

影响：

- 如果保留现实现状，需要更新《项目开发说明.md》§6 红线 5，否则后续评审会持续判定为偏离。
- TaskRunner 必须补齐 TurnOrchestrator 原本承担的一部分运行保障，例如工具作用域、模型选择、最大步数、超时、审计、错误传播和事件输出。
- chat 路径和 adaptive 路径成为两套执行核心，后续需要明确边界，避免能力漂移。

建议：

- 接受当前 TaskRunner 架构，并更新《项目开发说明.md》：红线 5 改为“TurnOrchestrator 接口不变；adaptive 不复用聊天 TurnOrchestrator，而通过 TaskRunner 直接驱动 ReActAgent，必须复用同一工具/LLM/guardrail 基础设施”。
- 明确 TaskRunner 的替代红线：不得写 message 表、必须写 artifact/event、必须强制 read_scope/write_scope、必须支持 supervisor abort、必须保证 max_steps/model_profile/allowed_tools 生效。
- 如果项目坚持原文档红线，则再投入 RunTurnOverrides 改造；否则不建议为了形式一致性强行套 TurnOrchestrator。

### 4. `write_scope` 没有在工具执行层强制约束

涉及文件：

- `server/src/forge/adaptive/validator.py`
- `server/src/forge/adaptive/task_runner.py`
- `server/src/forge/tools/executor.py`
- `server/src/forge/workspace/runtime.py`

现状：

- Validator 会校验 `write_scope` 在 workspace root 下。
- 但 TaskRunner 执行时只是 `chdir` 到 workspace/worktree。
- ToolExecutor 的 workspace policy 默认只限制路径在当前 workspace root 下，没有限制到 TaskNode 的 `write_scope`。
- 系统提示中要求“严禁越界访问”，但这不是强制安全边界。

影响：

- WRITE 任务声明 `write_scope=("server",)`，实际仍可能通过 `write_file` / `edit_file` 修改 worktree 下任意文件。
- 违反“write_scope 路径必须双重校验”和“write task 只允许写隔离范围”的设计红线。

建议：

- TaskRunner 为每个节点注入独立 ToolExecutor policy。
- ToolExecutor 支持 per-run/per-task allowed_roots，将写工具限制到 `write_scope`。
- READ 任务也应按 `read_scope` 限制读取范围，而不是只靠 prompt。

## P1 问题

### 5. WRITE 任务真实执行失败会被吞掉，仍可能标记完成

涉及文件：

- `server/src/forge/adaptive/executor.py`

现状：

- `_execute_write_in_isolation()` 中捕获 `TaskRunnerError` 后只写 warning。
- 随后仍然 collect worktree diff 并返回 PatchSet payload。
- 上层会把该任务标记为 COMPLETED。

影响：

- LLM/工具执行失败可能被包装成“成功但无 agent_output/空 diff”。
- 验证命令为空或弱验证时，run 可能最终 COMPLETED。

建议：

- WRITE TaskRunnerError 默认应使任务 FAILED。
- 如果需要保留部分 diff，应额外保存 failure artifact，但不能把任务标记为成功。

### 6. 真实 TaskRunner 使用全局 `chdir` 锁，实际并行能力被串行化

涉及文件：

- `server/src/forge/adaptive/task_runner.py`

现状：

- `run_node_with_react()` 对所有任务都进入 `_CHDIR_LOCK`。
- 在锁内执行完整 `agent.run()`。
- 进程级 cwd 是全局状态，所以同进程所有真实任务基本被串行化。

影响：

- M9 的 `asyncio.gather` 在真实执行模式下无法发挥并行能力。
- 多个 run 并发执行时，cwd 全局切换仍是高风险设计。
- 如果其他代码路径绕过 `_CHDIR_LOCK` 使用 cwd 相关工具，可能产生串扰。

建议：

- 不要依赖进程 cwd 表示 workspace。
- 工具执行器应显式接收 workspace root / read_scope / write_scope。
- ReActAgent 内部工具调用使用上下文对象，而不是全局 cwd。

### 7. `/api/v1/runs` 创建路径会产生重复 `run.created` 事件

涉及文件：

- `server/src/forge/api/routes/v1/runs.py`
- `server/src/forge/adaptive/orchestrator.py`

现状：

- `create_run()` 保存 run 后追加一次 `RUN_CREATED`。
- supervisor 启动 orchestrator 后，`AdaptiveRunOrchestrator.run()` 非 resume 路径又追加一次 `RUN_CREATED`。

影响：

- SSE 客户端会看到重复创建事件。
- 前端/CLI 如果按事件构建状态机，可能出现重复初始化或统计错误。

建议：

- 只保留 API 建档事件或 orchestrator 启动事件之一。
- 如果两个事件都需要，应拆分为 `run.created` 和 `run.started`。

### 8. 状态机仍保留“校验失败后直接覆盖”的降级路径

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/executor.py`

现状：

- `_set_status()` 优先调用 `store.transition_status()`。
- 但如果状态转换非法，会记录 warning 后直接覆盖 run.status。

影响：

- 状态机仍不是硬约束。
- 非法转换会被隐藏成 warning，线上数据可能进入无法解释的状态。

建议：

- 生产路径禁止 direct overwrite。
- 单测需要便利时可通过 test-only store 或显式参数控制。

### 9. `allow_write=False` 时验证失败仍会追加 WRITE 修复任务

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/executor.py`

现状：

- `_verify_and_repair()` 在验证失败后无条件 `_append_fix_task()`。
- `_append_fix_task()` 创建 WRITE 节点，allowed_tools 包含 `edit_file` / `write_file`。
- 如果 run.options.allow_write=False，Executor 会拒绝该任务，最终 run 进入 FAILED。

影响：

- 只读任务验证失败时，系统不应尝试自动写修复。
- 当前表现会从 VERIFY_FAILED 变成 repair_task_failed，错误语义不准确。

建议：

- `allow_write=False` 时验证失败应直接 BLOCKED 或 FAILED，并提示需要用户允许写入。
- 不应追加违反 options 的 synthetic task。

### 10. Integrator 仍缺少真实语义冲突处理，文本冲突判断也偏保守

涉及文件：

- `server/src/forge/adaptive/integrator.py`

现状：

- 同一文件被多个 PatchSet 修改时直接判 `text_conflict`。
- 不区分非重叠 hunk。
- 没有语义冲突检测。
- `git apply --check` 后再 apply 已降低半应用风险，但如果 check/apply 之间工作区被外部修改，仍没有 rollback。

影响：

- 可能过度 BLOCKED。
- 也可能漏掉接口不兼容、重复定义、行为冲突等语义问题。

建议：

- 在临时 integration worktree 中应用全部 patch，通过测试后再合入主工作区。
- 使用 git 三方合并或 index 检测真实 hunk 冲突。
- 将验证失败、类型检查失败、重复符号等纳入 semantic conflict report。

### 11. EXECUTE 任务的完成状态不能代表命令真实通过

涉及文件：

- `server/src/forge/adaptive/executor.py`
- `server/src/forge/adaptive/task_runner.py`
- `server/src/forge/tools/builtin/code/shell.py`

现状：

- fallback EXECUTE 直接返回 `passed=True`。
- real EXECUTE 只要 ReActAgent 完成，就设置 `passed=True`。
- 没有从 shell 工具结果中解析真实退出码作为 TaskStatus 的依据。

影响：

- TaskNode 级 EXECUTE 可能显示完成，但命令实际失败。
- 最终 verifier 可能兜底发现问题，但任务级 artifact 语义仍不准确。

建议：

- EXECUTE 节点必须有结构化命令结果：exit_code/stdout/stderr/passed。
- 失败命令应使任务 FAILED，除非 output_contract 明确允许“报告失败”。

### 12. 测试配置曾被 `server/.env` 污染，现已修复但需要持续约束

涉及文件：

- `server/.env`
- `server/config/_env.py`
- `server/config/sys_config.test.yaml`
- `server/config/sys_config.yaml`
- `server/config/sys_config.dev.yaml`
- `server/tests/unit/adaptive/test_options.py`
- `server/tests/unit/api/test_runs_and_artifacts_routes.py`
- `server/tests/unit/api/test_chat_task_mode_entry.py`

原现象：

- `server/.env` 中存在 `LLM_DEFAULT_MODEL=qwen3.6-plus`。
- `APP_ENV=test` 时，`load_env_files()` 仍会读取 `.env` 并填充未设置环境变量。
- test 配置的 dashscope provider 原本不包含 `qwen3.6-plus`。
- 因此 `get_settings()` 曾抛出配置校验错误。

当前处理：

- 已在 default/dev/test 的 dashscope provider 模型列表中补充 `qwen3.6-plus`。
- `server/tests/unit/adaptive` 已恢复通过。
- `server/tests/unit/api/test_runs_and_artifacts_routes.py server/tests/unit/api/test_chat_task_mode_entry.py` 已恢复通过。

剩余风险：

- `.env` 仍会影响 `APP_ENV=test` 的配置加载；未来新增默认模型时仍可能复发。
- 如果生产默认 provider 不是 dashscope，`model_profiles` 仍需要与 provider 配置同步。

建议：

- `.env.test` 明确覆盖所有会影响测试的 LLM 环境变量。
- CI 中显式设置 `LLM_DEFAULT_MODEL=qwen-plus` 或使用隔离配置。
- 增加配置加载 smoke test，覆盖 `.env` 存在时的 test 环境。

历史失败命令：

- `server/.venv/bin/pytest -q server/tests/unit/adaptive`
- `server/.venv/bin/pytest -q server/tests/unit/api/test_runs_and_artifacts_routes.py server/tests/unit/api/test_chat_task_mode_entry.py`

## P2 问题

### 13. RunSupervisor 完成后 handle 可能滞留

涉及文件：

- `server/src/forge/adaptive/supervisor.py`

现状：

- `_run_lifecycle()` 的 `finally` 中只有在 `handle.task.done()` 时才 pop。
- 该 finally 正在当前 task 内执行，此时 task 通常还未进入 done 状态。

影响：

- `_handles` 可能保留已完成 task，直到同 run_id 再次 start 时才被清理。
- 长时间运行进程中会有轻微内存泄漏和调试噪音。

建议：

- finally 中如果 handle.task is current_task，直接 pop。
- 或通过 task.add_done_callback 清理。

### 14. 查询类 API 省略 `workspace_path` 时会落到进程 cwd

涉及文件：

- `server/src/forge/api/routes/v1/runs.py`
- `server/src/forge/api/routes/v1/artifacts.py`

现状：

- 创建 run 和 chat task 已要求显式 workspace_path。
- 但 list/get/events/artifacts 查询端点如果省略 workspace_path，会使用 `Path.cwd()`。

影响：

- CLI/Web 如果没有持续传 workspace_path，可能查不到刚创建的 run。
- 多 workspace 场景下容易误查进程 cwd 对应的 state。

建议：

- 对查询端点也要求显式 workspace_path，或引入全局 run index。
- SSE URL 中应强制包含 workspace_path 或 run lookup 应跨 workspace。

## 已执行检查

- `server/.venv/bin/ruff check server/src/forge/adaptive server/src/forge/api/routes/v1/chat.py server/src/forge/api/routes/v1/runs.py server/src/forge/api/routes/v1/artifacts.py`
- 结果：通过。

- `server/.venv/bin/python -m py_compile server/src/forge/adaptive/executor.py server/src/forge/api/routes/v1/chat.py server/src/forge/api/routes/v1/runs.py`
- 结果：通过。

- `server/.venv/bin/pytest -q server/tests/unit/adaptive`
- 当前结果：通过，38 passed。

- `server/.venv/bin/pytest -q server/tests/unit/api/test_runs_and_artifacts_routes.py server/tests/unit/api/test_chat_task_mode_entry.py`
- 当前结果：通过，3 passed。

## 当前修复优先级

1. 修复 `planner_llm.py` 的错误 Message import，并补上 real LLM 最小集成测试。
2. 修复 `model_profiles` 与 provider 配置不匹配的长期结构问题。
3. 决定 adaptive 执行层到底复用 TurnOrchestrator 还是正式改文档为 TaskRunner。
4. 为 ToolExecutor 增加 per-task read_scope/write_scope 强制约束。
5. WRITE 任务真实执行失败必须让 TaskStatus=FAILED。
6. 去掉状态机 direct overwrite 降级路径。
7. 消除 TaskRunner 的全局 cwd 依赖，再验证 M9 并行执行。
