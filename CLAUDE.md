# Forge
所有代码注释 日志打印都必须是中文为主 必须以简体中文回复我

> 配套文档：[docs/architecture.md](docs/architecture.md)（架构 / 扩展点 / 解耦设计）、[docs/learning_path.md](docs/learning_path.md)（由浅入深代码阅读路线）。

## 1. 项目定位

Forge 的核心目标是：

- 输入自然语言目标；
- 经由规划、执行、工具调用、（CLI 端）合并验证等步骤；
- 产出可落地的结构化成果（对话回复、代码补丁、报告、事件流、运行产物）。

当前架构的**核心判断**：

> 以**单一 `ReActAgent` 内核**承载所有智能体执行，以**可组合的 `AgentLifecycle`** 作为唯一扩展机制；
> 所有模式（mode）差异——chat / plan_exec / workflow——都被**外置为 lifecycle 组合**，绝不在 `ReActAgent` 内部写 `if mode == ...` 分支。

基于这一内核，后端并存**两条执行路径**：

- **Chat 路径（Web 端）**：`/api/v1/chat/*`，`TurnOrchestrator` + `ChatTurnRun`（背景 asyncio.Task），落 `chat_messages` 数据库。
- **CLI 路径（plan_exec / workflow）**：`/api/v1/runs*` + `/api/v1/decisions*` + `/api/v1/artifacts*`，`RunOrchestrator` + `RunStore`（JSONL），支持 Plan/Workflow 的人机交互（HITL）。

> ⚠️ 重要变更：旧 **Adaptive 路径**（`adaptive/` 的 DAG / Validator / Wave 调度、git worktree 隔离、`AdaptiveRun` 状态机）**已整体删除**。其有用能力下沉为「普通工具 + 通用 RunStore + lifecycle」。本文与历史 Adaptive 描述不一致时，以本文为准。

---

## 2. 顶层目录与职责

```text
Forge/
├── server/  # FastAPI 后端（核心业务，唯一完整可运行单元）
├── web/     # React 前端（对话与管理界面）
├── docs/    # 设计文档（architecture.md / learning_path.md）
└── *.md     # 项目说明
```

关键观察：

- `server/` 是当前唯一完整可运行的业务核心。
- `web/` 已具备完整路由与对话能力，聊天链路成熟；管理/运行可视化部分仍在补齐。
- 顶层 `cli/` 目录已不存在；「CLI 端」指通过 `/v1/runs` API 驱动的任务执行客户端形态（plan_exec / workflow），后端能力已落地。

---

## 3. 后端架构（`server/src/forge`）

### 3.1 分层结构

- `api/`：FastAPI 路由、依赖注入、中间件、请求/响应 schema、`lifespan` 装配。
- `agents/`：**Agent 内核与扩展机制**——`ReActAgent`、`AgentLifecycle` 协议与组合器、Plan/Workflow/Persistence lifecycle、HITL、agent_profiles、角色、CLI 编排（`RunOrchestrator` / `RunSupervisor`）。
- `chat/`：**Chat 回合编排**——准备上下文、上下文压缩、执行 Runner、收尾入库、SSE 事件溯源（`ChatTurnRun` / `Broadcaster` / `ChatEventStore`）、LoopGuard 守护。
- `llm/`：`LLMGateway`、路由/调度链、Provider 注册与客户端池、中间件流水线。
- `tools/`：工具基类、注册中心、工具执行与 guardrail。
- `context/` + `context_mgmt/`：双层上下文构建系统（兼容层 + 新内核）。
- `memory/`：会话摘要与长期记忆（事件驱动写路径）。
- `retrieval/`：RAG 解析/切分/向量化/检索。
- `infrastructure/`：数据库、缓存、队列、事件总线、JSONL、`RunStore`、文件存储。
- `config/`：配置加载与域模型（`sys_config.*.yaml` 映射，含 `agent_profiles`）。
- `observability/`：日志、追踪、指标。

### 3.2 应用启动与运行时装配

应用入口：`server/src/forge/api/server.py`

- 创建 FastAPI 应用，注册 CORS、`ClientTypeMiddleware`、Tracing、ErrorHandler。
- 挂载 `api_router` 到 `/api`，业务前缀为 `/api/v1/*`。

启动装配：`server/src/forge/api/lifespan.py:42` `lifespan()`，装配顺序（即组件依赖图）：

```
Logging/Tracing → PromptRegistry → ToolRegistry + AGENT_ROLES
  → load_profiles_at_startup（依赖前三者就绪，7 项强校验，不过则拒绝启动）
  → Database → TaskQueue → EventBus + memory hooks → Redis + ModelConfigCache
  → LLMGateway → RAG 组件 → DecisionRegistry.cleanup_loop → ChatTurnSupervisor.cleanup
```

配置入口：`server/src/forge/config/settings.py`

- 配置优先级：`init_settings(path)` > `APP_CONFIG` > `APP_ENV` > 默认配置路径。
- `agent_profiles` 段定义所有 agent_mode 的工具白名单、模型档位、Plan/Workflow 开关与持久化目标。

### 3.3 API 功能域总览（v1）

聚合入口：`server/src/forge/api/routes/router.py`

当前实际挂载的业务域：

- **系统与健康**：`/system/health`、`/system/ready`
- **认证与会话身份**：`/auth/login`、`/auth/refresh`、`/auth/me`、`/auth/change-password`
- **用户管理（管理员）**：`/users`、`/users/{id}`、`/users/{id}/reset-password`
- **API Key 管理**：`/api-keys`
- **聊天会话**：`/sessions`、`/sessions/{id}/messages`
- **流式对话（Chat 路径）**：`/chat/completions`、`/chat/resume`、`/chat/stop`、`/chat/regenerate`、`/chat/quota`
- **任务运行（CLI 路径）**：`/runs`（创建/列表/查询）、`/runs/{id}/events`（SSE cursor 轮询）、`/runs/{id}/abort`
- **人机决策（HITL）**：`/decisions/{token}`（Plan 批准 / workflow_gate 通用入口）
- **运行产物**：`/artifacts`、`/artifacts/{id}`
- **知识库（KB）**：`/kb`、`/kb/{id}/documents`
- **模型与供应商治理**：`/models`、`/providers`、`/admin/*`
- **工具清单**：`/tools`

说明：

- `documents.py`、`retrieval.py`、`feedback.py` 路由文件存在但未在 `router.py` 挂载（占位/未启用）。
- HITL 决策统一走 `POST /v1/decisions/{token}`，旧 `/runs/{id}/decide` 已废弃。

### 3.4 功能分层调用链

典型链路：

- **Chat**：`/chat/completions` → `TurnOrchestrator.start_turn` → 建 `ChatTurnRun` + 背景 task（`_execute_new_turn`）→ `ContextAssembler` 组装/压缩 → `ReActRunner.from_profile` 跑 `ReActAgent.stream` → `TurnFinalizer` 落 DB。SSE 由路由订阅 `run.subscribe()`。
- **CLI runs**：`/runs` → `RunStore.create_run`（落档案）→ `RunOrchestrator` → `get_run_supervisor().register`（启背景 task）→ `RunOrchestrator._execute` 跑 `ReActAgent.stream`（装配 Guards + PlanMode/Workflow + Persistence lifecycle）。事件经 lifecycle 落 `events.jsonl`，客户端 `/runs/{id}/events` cursor 轮询。
- **HITL**：Plan/Workflow lifecycle 在 `before_tool_call` 创建 `PendingDecision` 并阻塞 → 客户端 `POST /v1/decisions/{token}` → `DecisionRegistry.resolve` 唤醒主 agent。
- **KB 上传**：`/kb/{id}/documents` → `KbService.upload_document` → `KbIngestService.ingest`（Saga：parsing→chunking→embedding→persist→indexed）。
- **模型治理**：`/models` → `AdminModelService` → Provider/Model Repo → `ModelConfigCache` + EventBus。

### 3.5 数据存储架构

「多存储协作」架构：

- **关系库（MySQL/SQLite）**：核心业务元数据（用户/鉴权/会话消息/KB/模型治理/摘要）。
- **Redis（可选默认接入）**：模型配置缓存、限流、幂等与精确缓存。
- **本地文件系统**：上传文档与 CLI 运行态文件。
- **向量库 + BM25 库**：知识检索索引。

关系库核心表（ORM）：

- 用户与鉴权：`users`、`refresh_token_blacklist`、`user_api_keys`
- 对话域：`chat_sessions`、`chat_messages`、`session_summaries`
- 知识库域：`knowledge_bases`、`kb_documents`、`kb_document_chunks`
- 模型治理域：`providers`、`provider_keys`、`models`

运行态存储分两类（均不入关系库）：

- **Chat turn 事件**：`chat_runs/<message_id>/`（`events.jsonl` + `state.json`），见 `chat/event_store.py`；最终内容落 `chat_messages` DB。
- **CLI run 运行态**：`RunStore`，per-workspace 的 `runs/<run_id>/`（`state.json` + `events.jsonl` + `artifacts/*.json`），见 `infrastructure/run_store.py`。

### 3.6 LLM 子系统（Gateway 化）

`llm/` 是网关化架构，不是简单 SDK 封装：

- 统一入口：`LLMGateway`（`llm/gateway.py:54`），业务层唯一对外入口。
- 请求流程：Pre 中间件（validator→rate_limit→budget→dedup→cache）→ Dispatcher（router→chain→熔断→重试→fallback）→ Provider → Post 中间件（cache_write→dedup_complete→audit）。
- 供应商适配：通过 registry 动态注册（openai/anthropic/google/dashscope/mock 等）。
- 对 agent 的适配：`GatewayLLMAdapter`（`llm/binding.py`）把网关包装成 `chat_with_tools_stream` facade，`ReActAgent` 只依赖 `ToolCallingLLM` Protocol，不感知 provider/路由/熔断。

chat / CLI / memory 等子系统共享这一套可观测、可治理的 LLM 调用能力。

### 3.7 RAG 与知识库子系统

RAG 由 `lifespan` 启动阶段动态装配，具备「软降级」能力：

- 解析：`retrieval/parsers/*`（pdf/word/text）
- 切分：`retrieval/chunkers/*`
- 向量化：`retrieval/embedders/*`
- 存储：`retrieval/stores/vector/*` + `retrieval/stores/bm25/*`
- 检索：`RetrieverFactory` + `knowledge_search` 工具

文档入库由 `KbIngestService` 以 Saga 风格编排：`parsing → chunking → embedding → persist → indexed`，失败时补偿清理并标记 `failed`。

### 3.8 后台任务、事件与并发机制

- **ChatTurnSupervisor**（`chat/supervisor.py`）：Chat turn 进程内注册表 + evict（终态留 10 分钟应付重连）+ 磁盘清理（7 天）+ 关停取消。
- **RunSupervisor**（`agents/run_supervisor.py`）：CLI run 进程内追踪 + abort/cancel + 关停取消。
- **TaskQueue**：默认 `LocalTaskQueue`，可切 Celery，失败降级 `NullTaskQueue`（memory 摘要任务用）。
- **EventBus**：默认进程内总线，用于 `turn.completed`（触发摘要）、模型配置变更等。
- **DecisionRegistry**（`agents/hitl.py`）：HITL 待决策注册表 + TTL `cleanup_loop`。
- **SSE**：Chat（broadcaster 实时 + events.jsonl 回放）/ CLI run（cursor 轮询）/ admin 三类事件流。

该设计适合单机/单进程先跑通全链路，再向多进程演进（in-memory 注册表后接 Redis pub/sub 即可平替）。

### 3.9 当前实现差异与缺口

- mode 路由已落地：通过 `agent_profiles` + `ReActRunner.from_profile` / `RunOrchestrator._build_lifecycles` 装配不同 lifecycle 组合。
- `/kb` 是真实知识库前缀；前端仍有 `knowledge-bases` 形态调用约定，需继续对齐。
- 前端 `agentsApi` 与页面入口存在，但后端未挂 `/agents` 业务路由。
- `documents.py` / `retrieval.py` / `feedback.py` 路由占位未挂载。
- 旧 `adaptive/` 与 `orchestration/workflow` 模块已删除。

### 3.10 上下文管理系统（Context）

「双层上下文架构」：

- **兼容入口层**：`forge.context`（chat 主链实际调用，`chat/assembler.py` 调 `forge.context.factory.build_context_builder()`）。
- **新一代内核层**：`forge.context_mgmt`（已落地，并通过适配器承接）。

`context_mgmt/types.py` 统一值对象：`ContextRequest` / `WindowBudget` / `ContextSnapshot` / `ContextUsage` / `CompactionResult`。

构建流水线（Fork-Join）：`BudgetPolicy.allocate` → 并行（`PromptRenderer.render` + `ContentGatherer.gather`）→ `MessageAssembler.assemble` → 输出 `ContextSnapshot`（带用量与降级信息）。`ContentGatherer` 用 `asyncio.gather(return_exceptions=True)`：summary/facts 失败进 `degraded`（软降级），history 失败硬抛。

扩展点：`ContentProvider` / `HistoryFilter` / `ToolResultPolicy` / `BudgetPolicy` / `TokenMeter`。compat 适配器为零退化，chat 现状默认仍是 `RecentFilter + VerbatimPolicy` 语义。

### 3.11 上下文压缩子系统（Compaction）

chat 主链由 `chat/assembler.py` `ContextAssembler` 主动压缩（`orchestrator.py:204` 调用）：

- 触发条件（`should_compact`）：`history_messages_dropped > 0` 或 `estimated_input_tokens / context_window > threshold`（默认 0.85）。
- 触发后：调 `SummaryService.summarize_session()` → 重建 context → 发 SSE `compaction_started` / `compaction_done`。
- 失败语义：摘要失败不终止主流程，回退到压缩前上下文，写 `degraded=compaction_failed`。

新链路 `context_mgmt` 的 `CompactionController`（Trigger + Strategy 解耦）已具备完整能力，但 chat 主流程当前仍直接调 `ContextAssembler`。

### 3.12 Memory 记忆系统

「读路径（同步）+ 写路径（事件驱动）」：

- **读路径**：Context 构建阶段通过 `MemoryStore` 读取 `get_summary` / `recall_facts`。`CompositeMemoryStore` 已接 `SummaryStore`；`FactStore` 仍为占位（`recall_facts` 返回空）；关闭时走 `NullMemoryStore`。
- **写路径**：`TurnFinalizer` 对话成功后 publish `turn.completed`（`finalizer.py:292`）→ `memory.hooks.install_memory_hooks` 订阅（`memory/hooks.py`）→ 满足 `every_n_turns` 阈值派发 `memory.summarize` 任务 → `SummaryService` 读近 N 条 → `Summarizer` 走 LLMGateway → `SummaryStore.upsert` 写 `session_summaries`。

写路径把 chat 请求与摘要写入解耦，避免拉长时延。策略层（`ConflictResolver` / `ForgettingPolicy` / `MemoryScope`）已定义，当前默认 NoOp。

### 3.13 存储系统（Storage Architecture）

「分层多存储」：

- **结构化元数据**：MySQL（dev 可切 SQLite），Route→Service→Repository→ORM。
- **运行态日志型（JSONL）**：核心原语 `infrastructure/jsonl.py`（append/iter/tail/iter_after + 原子写）。应用于 `cost.jsonl`、`audit.jsonl`、chat `events.jsonl`、CLI `runs/*/events.jsonl` 与 `state.json` / `artifacts/*.json`。
- **文件对象存储**：`FileStorage` 抽象，`LocalFileStorage` 为主实现（`{kb_id}/{doc_id}/{filename}`），S3 实现可选；DB 只存 `storage_path` 键。
- **检索索引**：`ChildVectorStore`（默认 Chroma）+ `BM25Store`（默认 sqlite_fts5），`RetrieverFactory` 组装 `ParentChildRetriever`。
- **成本与审计**：`CostTracker` 进程内累计 + 周期 flush 到 `cost.jsonl`；`AuditLog` 记录危险工具调用。

### 3.14 可观测性设计（Observability）

四条链路：日志、追踪、指标、成本/预算。

- **日志**：`observability.logging.setup_logging`，自动注入 `trace_id/user_id/client_type`，文本/JSON 双格式。
- **追踪**：`with span("name") as s: s.set(...)`，exporter 可切 `none/otel/langfuse`；agent 内核 stream/step/llm_call/tool 四级埋点，chat prepare/assemble/compact/finalize 均埋点。
- **指标**：重点在 LLM 指标（请求量、延迟、token、成本、熔断、预算超限），Prometheus 软依赖。`business/technical/cost_metrics` 仍占位。
- **成本与预算**：`CostTracker` + `BudgetConfig`（全局/默认用户/指定用户三级），与 `quota` 滚动窗口（5 小时/7 天）联动，超限抛 `LLMBudgetExceeded`。
- **事件与 SSE**：chat 事件流（delta/tool_call/reasoning/compaction/done/partial）、CLI run 事件流（lifecycle_attached/step_completed/artifact_created/plan_decision_required/workflow_gate_required/...）、admin 事件流。

---

## 4. Agent 内核与扩展机制（架构核心）

> 这是理解整个后端的钥匙。详见 [docs/architecture.md](docs/architecture.md)。

### 4.1 ReActAgent（内核）

`agents/react/agent.py:92` `ReActAgent`。经典 ReAct（Reason+Act），用 LLM 原生 function calling（非文本解析）。主入口 `stream()`（`agent.py:188`）。

单步执行序列（所有扩展点的挂载坐标系）：

```
on_start  →  for step:  resolve_tools → before_step → [LLM 流式]
                        → for tc: before_tool_call → 执行/veto → on_tool_result
                        → after_step
          →  on_complete / on_error
```

要点：`lifecycle=None` ⇒ 纯 ReAct；同 step 内连续 `parallelism_safe` 工具并行（`asyncio.gather`），unsafe 串行；`abort_event` 多点检查实现优雅中断。

### 4.2 AgentLifecycle + MultiLifecycle（唯一扩展机制）

`agents/lifecycle.py`：

- `AgentLifecycle` Protocol（`lifecycle.py:120`）：8 个 hook，全部默认 no-op，按需覆写、无需继承。
- `MultiLifecycle`（`lifecycle.py:193`）：三种合并策略——
  - 「首个非 None 胜出」：`resolve_tools` / `before_step` / `before_tool_call`（避免互相覆盖）。
  - 「pipeline 累计」：`on_tool_result`（依次替换，形成管道）。
  - 「全部都调 + 单 lifecycle 异常隔离」：`on_start` / `after_step` / `on_complete` / `on_error`。

### 4.3 mode 差异如何外置

mode = lifecycle 组合，由编排层装配（这就是全部 mode 路由逻辑）：

- chat：`[GuardLifecycleAdapter]`（`chat/runner.py:112`）
- plan_exec：`[Guards, PlanModeLifecycle, RunStorePersistenceLifecycle]`（`agents/run_orchestrator.py:196` `_build_lifecycles`）
- workflow：`[Guards, WorkflowLifecycle, RunStorePersistenceLifecycle]`

### 4.4 具体 lifecycle 实现

- **GuardLifecycleAdapter**（`chat/guards/lifecycle_adapter.py:26`）：把旧 LoopGuard 体系（`StepSafetyNet`/`StuckDetector`/`TokenBudgetGuard`/`WallClockGuard`）无侵入接入新协议。
- **PlanModeLifecycle**（`agents/plan_mode.py:52`）：`PLAN_MODE` ContextVar + `resolve_tools` 切 readonly/full schema（动态工具集）+ `before_tool_call` 拦截 `exit_plan_mode` 走 HITL 解锁。物理隔离而非靠 LLM 自律；不修改 ToolExecutor。
- **WorkflowLifecycle**（`agents/workflow_lifecycle.py:86`）：模板驱动 phase 流水线，拦截 `advance_phase`，命中 gate 走 `workflow_gate` HITL。
- **RunStorePersistenceLifecycle**（`agents/persistence_lifecycle.py:45`）：CLI 持久化投影；`after_step`→`step_completed`，`on_tool_result` 大产物（默认 >8KB）落 artifact 回灌占位，`on_complete/on_error`→`transition_status`。持久化失败不阻断主流程。

### 4.5 agent_profiles（配置即治理）

- 配置模型：`config/domains/agent_profiles.py:38` `AgentProfile`（pydantic，`extra="forbid"`）。
- 实际配置：`config/sys_config.dev.yaml:137` `agent_profiles` 段（chat / plan_exec / workflow）。
- 加载校验：`agents/profiles.py:26` `load_profiles_at_startup`，启动期 7 项强校验（工具注册/`readonly⊆allowed`/角色存在/`plan_mode_initial⇒exit_plan_mode`/`spawn_subagent⇔sub_agents`/模板存在/model_profile 定义），任一不过拒绝启动。
- 关键字段：`tools_allowed`/`readonly_tools`/`plan_mode_initial`/`persistence`(chat_db/run_store/none)/`requires_template`/`model_profile`(fast/smart/strong)/`large_artifact_threshold_bytes`。

新增一个 mode 无需写 Python：YAML 加 profile + prompt 模板（+ 如需特殊行为再写一个 lifecycle 并在编排层装配）。

### 4.6 HITL（人机交互）

`agents/hitl.py`：`DecisionRegistry`（单进程全局，`hitl.py:67`）管理 `PendingDecision`（自带 `asyncio.Event`）。主 agent `await event.wait()` 阻塞；外部 `POST /v1/decisions/{token}` 调 `resolve` 唤醒；`cleanup_loop` TTL 守护过期自动 reject。`kind`：`plan` / `workflow_gate` / `tool_confirm`。

### 4.7 工具系统

- 基类：`tools/base.py:33` `Tool(ABC)`，实现 `run`（CPU）或 `arun`（IO）之一；元数据 `parallelism_safe`/`dangerous`/`required_scope`/`allowed_roles`/`path_role_whitelist`。
- 注册：`tools/registry.py:29` `@register_tool`，注册期预计算 schema 缓存。
- 执行：`tools/executor.py` `ToolExecutor`，guardrail 流水线（access→permission→rate_limit→dangerous_op）+ workspace 路径策略。

### 4.8 角色（子 agent）

`agents/roles/factory.py:8` `AgentRole`，7 个内置角色（triage/developer/architect/reviewer/qa/ra/devops），声明 `allowed_tools`/`model_preference`/`can_write`/`write_path_prefixes`。动态扩展：`register_custom_agent_role`。子 agent 经 `spawn_subagent` 派发，受 profile `sub_agents_allowed` 白名单约束。

---

## 5. Chat 路径（Web 对话主链）

主入口：`POST /api/v1/chat/completions`（`api/routes/v1/chat.py:78`）。

核心流程（`chat/orchestrator.py` `TurnOrchestrator`）：

1. `TurnPreparer`：同步准备 session、用户消息、assistant 占位消息（message_id 是 SSE 协议头）。
2. 建 `ChatTurnRun` + `supervisor.register` + `attach_task`（背景执行立即启动），路由立即返回 SSE 订阅流。
3. 背景 task（`_execute_new_turn`）：发 `session_created/message_start` → `ContextAssembler` 组装 + 必要时压缩（`compaction_started/done`）→ `ReActRunner.from_profile` 跑 `ReActAgent.stream` 透传事件 → `TurnFinalizer` 落 DB + 发 `done/error/partial`。

特点：

- **执行与传输解耦**：agent 跑在独立 task，客户端断开 / 浏览器关闭 / SSE 链路死都不影响落库。
- **断线重连**：`run.subscribe(last_seq=N)` 先回放 `events.jsonl`（seq>last_seq）再接 broadcaster 实时；`baseline_seq` 去重防 resume 翻倍。
- **中断**：`/chat/stop` → `run.abort()` → `abort_event.set()` → agent break → finalizer 落 aborted。
- **续写**：`/chat/resume` 直接接入活跃 run 或启新 resume turn（`_execute_resume`，含 `ResumeStreamDedup` 流式去重）。
- 工具集受 profile `tools_allowed` 限制，chat 默认只读（`knowledge_search`/`time_tool`）。

---

## 6. CLI 路径（plan_exec / workflow 任务主链）

主入口：`POST /api/v1/runs`（`api/routes/v1/runs.py:102`）。

- 入参 `mode + goal + workspace_path (+ workflow_template)`；校验 `profile.persistence == "run_store"`（chat 模式禁走 /runs）。
- `RunStore.create_run` 落档案 → `RunOrchestrator` → `get_run_supervisor().register` 启背景 task。
- 事件流：`GET /runs/{id}/events?follow=true` cursor 轮询 `events.jsonl`（与 chat 的 broadcaster 不同）。
- 中止：`POST /runs/{id}/abort`。

执行（`agents/run_orchestrator.py` `RunOrchestrator._execute`）：

1. `_build_llm`（GatewayBinding，按 profile.model_profile）。
2. `_render_system_prompt`（profile 模板 + user_system_prompt + workspace_path + goal）。
3. `_build_lifecycles`（按 profile flag 装配 Guards + PlanMode/Workflow + Persistence）。
4. 跑 `ReActAgent.stream`；细颗粒事件经 lifecycle 落 `events.jsonl`，端到端 `done/error` 额外落 run 事件。

**plan_exec（Claude Code 风格 Plan-Exec）**：

- Plan 阶段 LLM 只见 `readonly_tools`（含 `exit_plan_mode`）；调 `exit_plan_mode` 触发 HITL，用户批准后 `PLAN_MODE` 关闭、写工具解锁进入 Exec。
- 拒绝则把反馈回灌，agent 调整计划后再次提交。

**workflow（模板驱动）**：

- LLM 按 phase 顺序执行，每完成一 phase 调 `advance_phase`；命中 gate 走 `workflow_gate` HITL；全部完成输出综合报告。

---

## 7. 运行时安全边界

- **工具白名单**：每个 mode 由 profile `tools_allowed` 限定，启动期校验工具均已注册。
- **动态工具集（Plan Mode）**：Plan 阶段物理不暴露写工具 schema，LLM 看不到也调不到——隔离优于自律。
- **HITL gate**：plan_exec 的写工具解锁、workflow 的 phase 推进都需用户决策（`/v1/decisions/{token}`）。
- **角色约束**：子 agent 受 `AgentRole.can_write` / `write_path_prefixes` 与 profile `sub_agents_allowed` 双重约束。
- **工具 guardrail**：`ToolExecutor` 的 access→permission→rate_limit→dangerous_op 流水线 + workspace 路径策略；危险工具进 `audit.jsonl`。
- **Chat 侧**：profile 限制为只读/低风险工具。
- **守护兜底**：LoopGuard（步数/死循环/token/墙钟）经 `GuardLifecycleAdapter` 对所有 mode 生效。

---

## 8. 前端架构（`web/`）

### 8.1 技术栈

- React 18 + TypeScript + Vite；React Router v6；TanStack Query；Zustand（认证）；Axios + fetch ReadableStream SSE。

### 8.2 分层

- `src/api/`：REST 客户端与 SSE 封装（统一鉴权、401 自动 refresh）。
- `src/pages/`：路由页面（聊天、知识库、设置、管理）。
- `src/hooks/useChatStream.ts`：对话流式状态机（send/abort/resume）。
- `src/store/auth.ts`：用户状态持久化与 token 内存策略。
- `src/types/index.ts`：后端契约类型（Chat SSE 事件等）。

### 8.3 前后端对齐情况

- **已对齐**：Chat SSE 事件与 `/chat/*`、会话与消息接口。
- **部分未对齐**：前端 `agentsApi` 与页面入口存在，但后端未挂 `/agents`；CLI runs 的可视化仍需补齐。

---

## 9. 当前版本结论（架构画像）

一句话总结：

- Forge 当前是**「单一 ReActAgent 内核 + 可组合 AgentLifecycle 扩展」**的智能体框架，承载 **Chat（Web）** 与 **CLI（plan_exec / workflow）** 两条执行路径。

工程成熟度：

- **后端**：内核 + 扩展机制 + 两条路径 + HITL + 持久化双轨已成闭环，可跑完整生命周期。
- **前端**：聊天链路成熟；任务运行可视化仍需补齐。
- **历史包袱**：旧 Adaptive / orchestration 模块已清理。

---

## 10. 推荐阅读路径（新人上手）

详见 [docs/learning_path.md](docs/learning_path.md)（含 `file:line` 跳转与自检标准）。速览：

1. `api/routes/router.py` + `api/lifespan.py`（路由全景 + 启动装配）
2. `agents/react/agent.py` `stream()`（内核：一个 step 怎么跑）
3. `agents/lifecycle.py` `MultiLifecycle`（扩展机制：架构的钥匙）
4. `chat/orchestrator.py` → `chat/turn_run.py` → `chat/supervisor.py`（Chat 主链）
5. `tools/base.py` + `llm/gateway.py` + `llm/binding.py`（能力层）
6. `agents/run_orchestrator.py` → `agents/plan_mode.py` → `agents/hitl.py`（CLI 进阶）
7. `agents/profiles.py` + `config/sys_config.dev.yaml` 的 `agent_profiles`（配置即治理）
</content>
