# 开发bug

审查日期：2026-05-22

审查范围：根据《项目开发说明.md》和 AGENTS.md，对 `server/` 端 adaptive 主流程、API 入口、状态机、执行器、隔离、集成、校验和测试稳定性进行核对。

## 总体结论

当前 `server/` 端已经有 M1-M9 的目录结构、数据模型、API 骨架和部分状态流转代码，但整体执行主流程尚未达到《项目开发说明.md》中定义的服务端完成标准。

主要偏差是：AdaptiveRun 的关键步骤仍以占位实现为主，真实的 Discovery Agent、Planner LLM、TaskExecutor 调用 TurnOrchestrator、PatchSet 生成、修复循环和运行恢复能力都没有完整闭环。因此 M9 完成后不能认为服务端代码已经实现完毕，目前更接近“流程骨架可跑通”，不是“可落地产物的完整服务端”。

## P0 问题

### 1. Executor 没有真实执行 Agent，也没有调用 TurnOrchestrator

涉及文件：

- `server/src/forge/adaptive/executor.py`
- `server/src/forge/adaptive/orchestrator.py`

预期：

- EXECUTE 阶段应由 Scheduler 按 wave 调度 TaskNode。
- 每个 TaskNode 应通过 TaskExecutor 调用 `TurnOrchestrator.run_turn(...)`，并通过类似 RunTurnOverrides 的方式限制工具、写入范围、最大步数、模型配置和系统提示。
- WRITE 任务应在隔离 worktree 中真实执行修改，然后收集 PatchSet。

现状：

- READ、EXECUTE、REVIEW、INTEGRATE 任务只是生成占位 artifact。
- WRITE 任务在隔离模式下只执行 `prepare -> collect -> cleanup`，没有在 worktree 中运行 Agent，也没有产生真实代码修改。
- 非隔离 WRITE 任务也只是返回占位 PatchSet。

影响：

- AdaptiveRun 可能显示 COMPLETED，但没有真实执行用户任务。
- PatchSet 通常为空，最终产物不可落地。
- M5、M6、M9 的核心目标没有真正达成。

建议：

- TaskExecutor 必须接入 TurnOrchestrator 或等价 Agent 执行层。
- WRITE 任务必须在隔离目录内执行，collect 前必须有真实执行结果。
- 对空 PatchSet 增加 acceptance_criteria 校验，避免假完成。

### 2. Discovery 和 Planner 仍是占位实现，没有按文档进行代码库探索和 LLM 结构化规划

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/planner.py`

预期：

- DISCOVER 阶段使用 READ-only ReActAgent 探索代码库，生成 DiscoveryReport。
- PLAN 阶段由 Planner LLM 使用 tool_use 强制输出结构化 TaskGraph JSON。

现状：

- `_discover()` 只生成固定文本报告，没有调用 Agent，也没有真实探索代码库。
- `Planner` 默认使用 `_fallback_plan()`，没有生产级 LLM 调用路径。
- 所谓 dynamic planning 实际上仍是硬编码三节点 fallback graph。

影响：

- TaskGraph 与真实代码库状态弱相关。
- Planner 无法根据 DiscoveryReport 做动态拆解。
- VALIDATE 阶段虽然存在，但校验的是占位计划。

建议：

- 实现 Discovery Agent，限制只读工具并输出结构化 DiscoveryReport。
- Planner 接入 LLM tool_use，并保留 fallback 只作为降级路径。
- 将 planner 原始输出、失败原因和重试过程保存为 artifact/event。

### 3. `/api/v1/runs` 只创建 Run，不启动执行；`decide continue` 也不会恢复执行

涉及文件：

- `server/src/forge/api/routes/v1/runs.py`

预期：

- `/api/v1/runs` 作为新增 runs API，应能支撑创建、查询、事件订阅、abort、decide 等完整运行生命周期。
- BLOCKED 后调用 decide continue 应能继续或恢复运行。

现状：

- `POST /api/v1/runs` 只持久化 CREATED 状态的 run，不启动 AdaptiveRunOrchestrator。
- `/runs/{id}/decide` 的 continue 只把 BLOCKED 改成 PLANNING，没有后台任务接管继续执行。

影响：

- 通过 runs API 创建的任务不会真正运行。
- CLI 或 Web 如果依赖 `/runs` 创建任务，会得到一个静止 run。
- 人工决策 API 只改变状态，不改变业务执行结果。

建议：

- 明确 `/runs` 是纯 CRUD 还是任务启动入口。
- 如果是启动入口，创建后应投递后台任务并返回 run_id。
- decide continue 应携带决策信息并恢复 orchestration，而不是只改状态。

### 4. Verifier 修复循环会重新执行整个 TaskGraph，并重复集成历史 PatchSet

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/integrator.py`

预期：

- VERIFY 失败后应生成修复 task，回到 Step 4 执行修复任务，再集成新增 PatchSet。
- 修复循环应避免重复执行已完成任务和重复应用旧补丁。

现状：

- `_verify_and_repair()` 在失败后向原 TaskGraph 追加一个 synthetic fix node。
- 随后调用 `executor.execute(run, graph)`，会再次调度整个图，而不是只执行修复节点。
- Integrator 每次读取 run 下所有 PATCH_SET artifact，没有区分本轮新增 patch 和已集成 patch。

影响：

- 已完成任务可能被重复执行。
- 已应用补丁可能被重复 apply。
- 修复循环容易制造假冲突、重复变更或状态污染。

建议：

- 引入 execution attempt / integration batch 标识。
- 修复任务应形成新的子图或增量 wave，只执行未完成或新增节点。
- Integrator 只处理本轮未集成 PatchSet，并记录 integrated artifact id。

## P1 问题

### 5. Integrator 不是原子应用，失败时可能留下半集成工作区

涉及文件：

- `server/src/forge/adaptive/integrator.py`

预期：

- INTEGRATE 阶段应合并所有 PatchSet，处理文本冲突和语义冲突。
- 失败时应能生成 ConflictReport，且不污染主工作区。

现状：

- PatchSet 逐个 `git apply` 到 root_path。
- 如果前一个 patch apply 成功、后一个 patch 失败，当前代码没有回滚已应用 patch。
- 同文件变更直接判为冲突，无法区分非重叠 hunk。
- 没有语义冲突检测。

影响：

- BLOCKED 后工作区可能已经被部分修改。
- 非重叠同文件修改会被过度阻塞。
- 隐藏语义冲突无法识别。

建议：

- 在临时集成分支或临时 worktree 中 apply 全部 patch，通过后再合入主工作区。
- 使用三方合并或 git index 检测真实文本冲突。
- 语义冲突至少应结合测试失败、重复符号、接口签名变更等信号生成报告。

### 6. Orchestrator 绕过了 RunStatus 状态机校验

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/store.py`
- `server/src/forge/adaptive/models.py`

预期：

- RunStatus 应按 `CREATED -> PLANNING -> VALIDATING -> EXECUTING -> INTEGRATING -> VERIFYING -> COMPLETED` 流转。
- 非法状态转换应被拒绝。

现状：

- `AdaptiveRunStore.transition_status()` 有状态转换校验。
- 但 Orchestrator 的 `_set_status()` 直接修改 `run.status` 并保存，绕过 `can_transition_to()`。

影响：

- 核心执行流可能产生非法状态转换但不会被发现。
- 状态机规则只对 API 层部分操作生效，对主流程不生效。

建议：

- Orchestrator 状态变更统一走 store 的状态转换方法。
- 需要允许的特殊转换应显式加入模型规则，而不是绕过校验。

### 7. `allow_write=False` 没有被 Validator 和 Executor 强制执行

涉及文件：

- `server/src/forge/adaptive/options.py`
- `server/src/forge/adaptive/validator.py`
- `server/src/forge/adaptive/executor.py`

预期：

- TaskOptions 中如果禁止写入，Planner 即使生成 WRITE 节点也应被拒绝。
- Executor 也应二次防护，避免绕过 Planner/Validator。

现状：

- fallback planner 会根据 `allow_write` 决定是否生成 WRITE 节点。
- 但 Validator 没有基于 TaskOptions 拒绝 WRITE kind、写工具或非空 write_scope。
- Executor 也没有读取 `allow_write` 做硬拦截。

影响：

- 如果未来接入 LLM Planner 或自定义 planner，可能在 `allow_write=False` 时仍执行写任务。
- 违反“Planner 只能声明资源，资源必须过 Validator”的红线。

建议：

- Validator 增加 TaskOptions 上下文校验。
- Executor 在执行 WRITE 前再次校验 run.options.allow_write。

### 8. SSE 在传入过期 `after_event_id` 时可能永远收不到事件

涉及文件：

- `server/src/forge/adaptive/store.py`
- `server/src/forge/api/routes/v1/runs.py`

预期：

- `/runs/{id}/events` 应支持 after_event_id 续传。
- 如果传入的 event id 不存在，应有明确降级策略或错误响应。

现状：

- `JsonlLog.iter_after()` 如果找不到 after_event_id，会返回空迭代。
- SSE follow 模式会反复用同一个无效 cursor 查询，导致后续事件也无法推送。

影响：

- 客户端本地缓存过期或传错 id 后，事件流会表现为“连接正常但永远无消息”。

建议：

- after_event_id 不存在时返回 400，或降级为从末尾/开头读取。
- follow 循环应维护文件 offset 或最新事件 id，而不是依赖无效 cursor。。

## P2 问题

### 10. TASK_GRAPH artifact 没有落盘

涉及文件：

- `server/src/forge/adaptive/orchestrator.py`
- `server/src/forge/adaptive/models.py`

预期：

- PLAN 阶段应产出 `task_graph` artifact。

现状：

- `ArtifactKind.TASK_GRAPH` 已定义，但 plan/validate 后没有保存 TaskGraph artifact。

影响：

- Web/CLI 无法基于 artifact 查询原始任务图。
- 排查 planner 输出和 validator 失败原因不方便。

建议：

- plan 成功后保存 TaskGraph artifact。
- 每次 replan 都应保存 attempt 编号和失败原因。

### 11. `mode=auto` 路由到 simple 时实际仍走 chat 路径

涉及文件：

- `server/src/forge/adaptive/mode_router.py`
- `server/src/forge/api/routes/v1/chat.py`

预期：

- ModeRouter 应支持 chat、simple、adaptive 三路路由。
- simple 表示单 Agent 任务。

现状：

- `mode=auto` 可能返回 `simple`。
- chat API 中 `target == "adaptive"` 才走 adaptive，其余都落回 chat。

影响：

- simple 模式没有实现，自动路由结果与实际执行路径不一致。
- 用户以为进入 task/simple，实际可能走普通聊天路径。

建议：

- 实现 simple 单 Agent 执行路径。
- 或暂时让 ModeRouter 不返回 simple，避免误导。

### 12. 部分 TaskOptions 硬约束没有贯穿执行链路

涉及文件：

- `server/src/forge/adaptive/options.py`
- `server/src/forge/adaptive/executor.py`
- `server/src/forge/adaptive/orchestrator.py`

问题点：

- `allow_parallel=False` 没有明确阻止同 wave `asyncio.gather`。
- `max_agents` 没有作为并发上限控制 Scheduler。
- `max_run_duration_sec` 没有全局超时控制。
- `max_task_retries` 没有在 TaskExecutor 层实现。

影响：

- 用户传入的 TaskOptions 与实际执行行为不一致。
- 未来接入真实 Agent 后可能出现并发过高、运行失控或重试策略缺失。

建议：

- Scheduler 根据 `allow_parallel` 和 `max_agents` 控制并发。
- Orchestrator 增加 run 级 timeout。
- Executor 增加 task retry 逻辑并记录每次失败 artifact/event。

### 13. PatchSet 中记录的 worktree_path 在 cleanup 后已经失效

涉及文件：

- `server/src/forge/adaptive/workspace.py`
- `server/src/forge/adaptive/executor.py`

现状：

- WRITE 任务 collect 后会 cleanup worktree。
- PatchSet payload 中仍记录 `worktree_path`。

影响：

- 后续 API/CLI/Web 如果尝试打开该路径，会发现目录已不存在。

建议：

- 如果 worktree 只是临时目录，artifact 中应标注 `worktree_retained=false`。
- 如需调试失败任务，应支持失败时保留 worktree。

### 14. abort 非法状态转换可能返回 500

涉及文件：

- `server/src/forge/api/routes/v1/runs.py`

现状：

- `/runs/{id}/abort` 调用 `transition_status()`。
- 如果当前状态不允许 ABORTED，`ValueError` 没有被转换为 HTTP 4xx。

影响：

- 对已完成或已失败 run 调用 abort 可能返回 500。

建议：

- 捕获状态转换异常，返回 409 Conflict 或 400 Bad Request。

## 已执行检查

- `server/.venv/bin/ruff check server/src/forge/adaptive/*.py server/src/forge/api/routes/v1/chat.py server/src/forge/api/routes/v1/runs.py server/src/forge/api/routes/v1/artifacts.py`
- 结果：通过。

- `server/.venv/bin/pytest -q server/tests/unit/adaptive`
- 结果：失败，32 passed，3 failed。失败原因是配置默认模型 `qwen3.6-plus` 不在 provider 配置中。

- `server/.venv/bin/pytest -q server/tests/unit/api/test_runs_and_artifacts_routes.py server/tests/unit/api/test_chat_task_mode_entry.py`
- 结果：collection 阶段失败。失败原因同样是配置默认模型 `qwen3.6-plus` 不在 provider 配置中。

## 修复优先级建议

1. 接入真实 Discovery Agent 和 Planner LLM，保存 TaskGraph artifact。
2. 改造 TaskExecutor，确保所有任务通过受限 TurnOrchestrator/Agent 真实执行。
3. 修复 verifier repair loop，只执行新增修复任务并只集成新增 PatchSet。
4. 将 Integrator 改为临时 worktree 原子集成。
5. 统一状态机转换入口，禁止 Orchestrator 绕过状态机。
6. 补齐 runs API 的启动、恢复、abort 错误处理和 SSE cursor 语义。
