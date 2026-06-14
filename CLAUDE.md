# Forge
所有代码注释 日志打印都必须是中文为主 必须以简体中文回复我 思考过程也只能使用中文展示

> 配套文档：[docs/architecture.md](docs/architecture.md)（架构 / 扩展点 / 解耦设计）、[docs/learning_path.md](docs/learning_path.md)（由浅入深代码阅读路线）。

## 1. 项目定位

Forge 的核心目标是：

- 输入自然语言目标；
- 经由规划、执行、工具调用等步骤；
- 产出可落地的结构化成果（对话回复、报告、事件流）。

当前架构的**核心判断**：

> 以**单一 `ReActAgent` 内核**承载所有智能体执行，以**可组合的 `AgentLifecycle`** 作为唯一扩展机制；
> 所有模式（mode）差异都被**外置为 lifecycle 组合**，绝不在 `ReActAgent` 内部写 `if mode == ...` 分支。

基于这一内核，服务端的职责边界：

- **Chat 路径（Web 端，唯一智能体执行路径）**：`/api/v1/chat/*`，`TurnOrchestrator` + `ChatTurnRun`（背景 asyncio.Task），落 `chat_messages` 数据库。
- **对外 LLM 网关端点（供 CLI / 第三方客户端直连）**：`/api/v1/llm/chat/completions`，X-API-Key 鉴权，OpenAI 兼容格式，配额/预算/限流/缓存/审计由 LLMGateway 中间件按 user 自动生效。

> ⚠️ 重要变更（2026-06）：旧 **CLI 执行路径**（`/v1/runs` + `/v1/decisions` + `/v1/artifacts`、`RunOrchestrator`/`RunSupervisor`/`RunStore`、`PlanModeLifecycle`/`WorkflowLifecycle`/`RunStorePersistenceLifecycle`、HITL `DecisionRegistry`）**已整体删除**。未来 CLI 形态为**胖客户端**：agent loop 在用户本机运行、工具直接操作本地文件、人机交互走本地终端；服务端仅提供认证 + 模型治理 + 网关化 LLM 调用。更早的 Adaptive 路径（DAG/Validator/Wave 调度）同样早已删除。本文与历史描述不一致时，以本文为准。

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
- `web/` 已具备完整路由与对话能力，聊天链路成熟；管理可视化部分仍在补齐。
- 「CLI 客户端」尚未存在；落地时应以共享 `forge.agents` 内核包 + 本地 lifecycle 装配 + `/v1/llm/chat/completions` 直连的形态实现。

---

## 3. 后端架构（`server/src/forge`）

### 3.1 分层结构

- `api/`：FastAPI 路由、依赖注入、中间件、请求/响应 schema、`lifespan` 装配。
- `agents/`：**Agent 内核与扩展机制**——`ReActAgent`、`AgentLifecycle` 协议与组合器、agent_profiles。
- `chat/`：**Chat 回合编排**——准备上下文、上下文压缩、执行 Runner、收尾入库、SSE 事件溯源（`ChatTurnRun` / `Broadcaster` / `ChatEventStore`）、LoopGuard 守护。
- `llm/`：`LLMGateway`、路由/调度链、Provider 注册与客户端池、中间件流水线。
- `tools/`：工具基类、注册中心、工具执行与 guardrail。
- `context/` + `context_mgmt/`：双层上下文构建系统（兼容层 + 新内核）。
- `memory/`：会话摘要与长期记忆（事件驱动写路径）。
- `retrieval/`：RAG 解析/切分/向量化/检索。
- `infrastructure/`：数据库、缓存、队列、事件总线、JSONL、文件存储。
- `config/`：配置加载与域模型（`sys_config.*.yaml` 映射，含 `agent_profiles`）。
- `observability/`：日志、追踪、指标。

### 3.2 应用启动与运行时装配

应用入口：`server/src/forge/api/server.py`

- 创建 FastAPI 应用，注册 CORS、`ClientTypeMiddleware`、Tracing、ErrorHandler。
- 挂载 `api_router` 到 `/api`，业务前缀为 `/api/v1/*`。

启动装配：`server/src/forge/api/lifespan.py` `lifespan()`，装配顺序（即组件依赖图）：

```
Logging/Tracing → PromptRegistry → ToolRegistry + AGENT_ROLES
  → load_profiles_at_startup（依赖前三者就绪，5 项强校验，不过则拒绝启动）
  → Database → TaskQueue → EventBus + memory/digest/recall hooks
  → Redis + ModelConfigCache → LLMGateway → RAG 组件 → ChatTurnSupervisor.cleanup
```

配置入口：`server/src/forge/config/settings.py`

- 配置优先级：`init_settings(path)` > `APP_CONFIG` > `APP_ENV` > 默认配置路径。
- `agent_profiles` 段定义所有 agent_mode 的工具白名单、模型档位与持久化目标（当前仅 `chat`）。

### 3.3 API 功能域总览（v1）

聚合入口：`server/src/forge/api/routes/router.py`

当前实际挂载的业务域：

- **系统与健康**：`/system/health`、`/system/ready`
- **认证与会话身份**：`/auth/login`、`/auth/refresh`、`/auth/me`、`/auth/change-password`
- **用户管理（管理员）**：`/users`、`/users/{id}`、`/users/{id}/reset-password`
- **API Key 管理**：`/api-keys`
- **聊天会话**：`/sessions`、`/sessions/{id}/messages`
- **流式对话（Chat 路径）**：`/chat/completions`、`/chat/resume`、`/chat/stop`、`/chat/regenerate`、`/chat/quota`
- **对外 LLM 网关**：`/llm/chat/completions`（X-API-Key 鉴权，OpenAI 兼容，供 CLI/第三方直连）
- **知识库（KB）**：`/kb`、`/kb/{id}/documents`
- **模型与供应商治理**：`/models`、`/providers`、`/admin/*`
- **工具清单**：`/tools`

说明：

- `documents.py`、`retrieval.py`、`feedback.py` 路由文件存在但未在 `router.py` 挂载（占位/未启用）。

### 3.4 功能分层调用链

典型链路：

- **Chat**：`/chat/completions` → `TurnOrchestrator.start_turn` → 建 `ChatTurnRun` + 背景 task（`_execute_new_turn`）→ `ContextManager` 组装 + 按需压缩 → `ReActRunner.from_profile` 跑 `ReActAgent.stream` → `TurnFinalizer` 落 DB。SSE 由路由订阅 `run.subscribe()`。
- **对外 LLM 直连**：`/llm/chat/completions` → `ApiKeyUser` 鉴权 → 构造 `LLMRequest`（model 三形态：空=默认链 / `fast|smart|strong`=档位链 / `provider:model`=显式 pin）→ `LLMGateway.complete*/stream*` → OpenAI 风格 JSON / SSE chunk。
- **KB 上传**：`/kb/{id}/documents` → `KbService.upload_document` → `KbIngestService.ingest`（Saga：parsing→chunking→embedding→persist→indexed）。
- **模型治理**：`/models` → `AdminModelService` → Provider/Model Repo → `ModelConfigCache` + EventBus。

### 3.5 数据存储架构

「多存储协作」架构：

- **关系库（MySQL/SQLite）**：核心业务元数据（用户/鉴权/会话消息/KB/模型治理/摘要）。
- **Redis（可选默认接入）**：模型配置缓存、限流、幂等与精确缓存。
- **本地文件系统**：上传文档与 chat 运行态事件。
- **向量库 + BM25 库**：知识检索索引。

关系库核心表（ORM）：

- 用户与鉴权：`users`、`refresh_token_blacklist`、`user_api_keys`
- 对话域：`chat_sessions`、`chat_messages`、`session_summaries`
- 知识库域：`knowledge_bases`、`kb_documents`、`kb_document_chunks`
- 模型治理域：`providers`、`provider_keys`、`models`

运行态存储（不入关系库）：

- **Chat turn 事件**：`chat_runs/<message_id>/`（`events.jsonl` + `state.json`），见 `chat/event_store.py`；最终内容落 `chat_messages` DB。

### 3.6 LLM 子系统（Gateway 化）

`llm/` 是网关化架构，不是简单 SDK 封装：

- 统一入口：`LLMGateway`（`llm/gateway.py`），业务层唯一对外入口。
- 请求流程：Pre 中间件（validator→rate_limit→budget→dedup→cache）→ Dispatcher（router→chain→熔断→重试→fallback）→ Provider → Post 中间件（cache_write→dedup_complete→audit）。
- 供应商适配：通过 registry 动态注册（openai/anthropic/google/dashscope/mock 等）。
- 对 agent 的适配：`GatewayLLMAdapter`（`llm/binding.py`）把网关包装成 `chat_with_tools_stream` facade，`ReActAgent` 只依赖 `ToolCallingLLM` ABC，不感知 provider/路由/熔断。
- **对外开放**：`api/routes/v1/llm.py` 把网关以 OpenAI 兼容 HTTP 端点形式暴露给外部客户端（API Key 鉴权 + user 级配额/预算/审计）。

chat / memory / 外部客户端共享这一套可观测、可治理的 LLM 调用能力。

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
- **TaskQueue**：默认 `LocalTaskQueue`，可切 Celery，失败降级 `NullTaskQueue`（memory 摘要任务用）。
- **EventBus**：默认进程内总线，用于 `turn.completed`（触发摘要/digest/语义召回）、模型配置变更等。
- **SSE**：Chat（broadcaster 实时 + events.jsonl 回放）/ admin 两类事件流。

该设计适合单机/单进程先跑通全链路，再向多进程演进（in-memory 注册表后接 Redis pub/sub 即可平替）。

### 3.9 当前实现差异与缺口

- mode 路由已落地：通过 `agent_profiles` + `ReActRunner.from_profile` 装配 lifecycle 组合（当前仅 chat）。
- `/kb` 是真实知识库前缀；前端仍有 `knowledge-bases` 形态调用约定，需继续对齐。
- 前端 `agentsApi` 与页面入口存在，但后端未挂 `/agents` 业务路由。
- `documents.py` / `retrieval.py` / `feedback.py` 路由占位未挂载。
- 旧 CLI 执行路径 / `adaptive/` / `orchestration/workflow` 模块已删除。
- CLI 胖客户端尚未开工；落地时复用 `forge.agents` 内核 + `/v1/llm/chat/completions`。

### 3.10 上下文管理系统（Context）

chat 主链直接调用 `forge.context_mgmt`（`chat/orchestrator.py` 调 `context_mgmt.builder.factory.build_context_builder()`，旧 `forge.context` 兼容层已删除）。

`context_mgmt/types.py` 统一值对象：`ContextRequest` / `WindowBudget` / `ContextSnapshot` / `ContextUsage` / `CompactionResult`。

构建流水线（Fork-Join）：`BudgetPolicy.allocate` → 并行（`PromptRenderer.render` + `ContentGatherer.gather`）→ `MessageAssembler.assemble` → 输出 `ContextSnapshot`（带用量与降级信息）。`ContentGatherer` 用 `asyncio.gather(return_exceptions=True)`：summary/facts 失败进 `degraded`（软降级），history 失败硬抛。

扩展点：`ContentProvider` / `HistoryFilter` / `ToolResultPolicy` / `BudgetPolicy` / `TokenMeter`。服务端仅 chat 一条路径，默认装配 `HybridFilter`（近期锚点 + 语义过滤）+ `TruncatingPolicy` + `DefaultBudgetPolicy`（已移除历史的 ContextMode 多模式分支）。

### 3.11 上下文压缩子系统（Compaction）

chat 主链由 `context_mgmt` 的 `ContextManager` + `CompactionController`（Trigger + Strategy 解耦）主动压缩，`TurnOrchestrator.build` 时编排：

- 触发条件（`ThresholdTrigger.should_compact`）：`history_messages_dropped > 0` 或 `total_ratio > threshold`（默认 0.85）。
- 触发后：`SummaryCompaction` 调 `SummaryService.summarize_session()` → `ContextManager` 重跑 builder 重建 context → 发 SSE `compaction_started` / `compaction_done`。
- 失败语义：摘要失败不终止主流程，回退到压缩前上下文，写 `degraded=compaction_failed`。

### 3.12 Memory 记忆系统

「读路径（同步）+ 写路径（事件驱动）」：

- **读路径**：Context 构建阶段通过 `MemoryStore` 读取 `get_summary` / `recall_facts`。`CompositeMemoryStore` 接 `SummaryStore` + `FactStore`（`memory/facts/store.py`：`user_facts` 表 + int8 量化向量按 user 暴力余弦召回，`memory.facts.enabled` 灰度开关）；关闭时走 `NullMemoryStore`。
- **写路径**：`TurnFinalizer` 对话成功后 publish `turn.completed` → `memory.hooks.install_memory_hooks` 订阅（`memory/hooks.py`）→ 按各自阈值派发两类任务：`memory.summarize`（**增量滚动摘要**：旧摘要 + `covered_until_message_id` 水位后新消息喂 LLM 融合 → `SummaryStore.upsert` 写 `session_summaries`）与 `memory.extract_facts`（`FactExtractionService` 读 `fact_extraction_watermarks` 水位 → LLM 结构化抽取用户长期事实 → `ThresholdDedupResolver` 相似度去重 → 写 `user_facts`，带 `source_session_id` 溯源）。

写路径把 chat 请求与摘要/抽取写入解耦，避免拉长时延。策略层：`ConflictResolver` 事实层默认 `ThresholdDedupResolver`；`ForgettingPolicy` 仍为 NoOp。会话删除时 `SessionService.delete` 级联清理会话摘要（用户事实不随会话删除）。

### 3.13 存储系统（Storage Architecture）

「分层多存储」：

- **结构化元数据**：MySQL（dev 可切 SQLite），Route→Service→Repository→ORM。
- **运行态日志型（JSONL）**：核心原语 `infrastructure/jsonl.py`（append/iter/tail/iter_after + 原子写）。应用于 `cost.jsonl`、`audit.jsonl`、chat `events.jsonl`。
- **文件对象存储**：`FileStorage` 抽象，`LocalFileStorage` 为主实现（`{kb_id}/{doc_id}/{filename}`），S3 实现可选；DB 只存 `storage_path` 键。
- **检索索引**：`ChildVectorStore`（默认 Chroma）+ `BM25Store`（默认 sqlite_fts5），`RetrieverFactory` 组装 `ParentChildRetriever`。
- **成本与审计**：`CostTracker` 进程内累计 + 周期 flush 到 `cost.jsonl`；`AuditLog` 记录危险工具调用。
- **内容引用切片**：`ContentStore`（`infrastructure/storage/content_store.py`），chat 后端 `DbMessageContentStore` 支撑 digest 引用占位的按需回读（`read_message` 工具）。

### 3.14 可观测性设计（Observability）

四条链路：日志、追踪、指标、成本/预算。

- **日志**：`observability.logging.setup_logging`，自动注入 `trace_id/user_id/client_type`，文本/JSON 双格式。
- **追踪**：`with span("name") as s: s.set(...)`，exporter 可切 `none/otel/langfuse`；agent 内核 stream/step/llm_call/tool 四级埋点，chat prepare/assemble/compact/finalize 均埋点。
- **指标**：重点在 LLM 指标（请求量、延迟、token、成本、熔断、预算超限），Prometheus 软依赖。`business/technical/cost_metrics` 仍占位。
- **成本与预算**：`CostTracker` + `BudgetConfig`（全局/默认用户/指定用户三级），与 `quota` 滚动窗口（5 小时/7 天）联动，超限抛 `LLMBudgetExceeded`。
- **事件与 SSE**：chat 事件流（delta/tool_call/reasoning/compaction/done/partial）、admin 事件流。

---

## 4. Agent 内核与扩展机制（架构核心）

> 这是理解整个后端的钥匙。详见 [docs/architecture.md](docs/architecture.md)。

### 4.1 ReActAgent（内核）

`agents/react/agent.py` `ReActAgent`。经典 ReAct（Reason+Act），用 LLM 原生 function calling（非文本解析）。主入口 `stream()`。

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

- `AgentLifecycle` ABC：8 个 hook 全部抽象；按需覆写的实现继承 `NoopLifecycle`，由其提供默认 no-op。
- `MultiLifecycle`：三种合并策略——
  - 「首个非 None 胜出」：`resolve_tools` / `before_step` / `before_tool_call`（避免互相覆盖）。
  - 「pipeline 累计」：`on_tool_result`（依次替换，形成管道）。
  - 「全部都调 + 单 lifecycle 异常隔离」：`on_start` / `after_step` / `on_complete` / `on_error`。

### 4.3 mode 差异如何外置

mode = lifecycle 组合，由编排层装配（这就是全部 mode 路由逻辑）：

- chat：`[GuardLifecycleAdapter]`（`chat/runner.py`）

当前服务端仅 chat 一个 mode；机制保留，未来新增 mode（或 CLI 客户端本地装配 Plan/Workflow 类 lifecycle）时按同样方式扩展，无需改内核。

### 4.4 具体 lifecycle 实现

- **GuardLifecycleAdapter**（`chat/guards/lifecycle_adapter.py`）：把旧 LoopGuard 体系（`StepSafetyNet`/`StuckDetector`/`TokenBudgetGuard`/`WallClockGuard`）无侵入接入新协议。

### 4.5 agent_profiles（配置即治理）

- 配置模型：`config/domains/agent_profiles.py` `AgentProfile`（pydantic，`extra="forbid"`）。
- 实际配置：`config/sys_config.dev.yaml` `agent_profiles` 段（当前仅 chat）。
- 加载校验：`agents/profiles.py` `load_profiles_at_startup`，启动期 3 项强校验（工具注册/模板存在/model_profile 定义），任一不过拒绝启动。
- 关键字段：`tools_allowed`/`max_steps`/`model_profile`(fast/smart/strong)。

新增一个 mode 无需写 Python：YAML 加 profile + prompt 模板（+ 如需特殊行为再写一个 lifecycle 并在编排层装配）。

### 4.6 工具系统

- 基类：`tools/base.py` `Tool(ABC)`，实现 `run`（CPU）或 `arun`（IO）之一；元数据 `parallelism_safe`/`dangerous`/`required_scope`/`allowed_roles`/`path_role_whitelist`。
- 注册：`tools/registry.py` `@register_tool`，注册期预计算 schema 缓存。
- 执行：`tools/executor.py` `ToolExecutor`，guardrail 流水线（access→permission→rate_limit→dangerous_op）+ workspace 路径策略。

---

## 5. Chat 路径（Web 对话主链）

主入口：`POST /api/v1/chat/completions`（`api/routes/v1/chat.py`）。

核心流程（`chat/orchestrator.py` `TurnOrchestrator`）：

1. `TurnPreparer`：同步准备 session、用户消息、assistant 占位消息（message_id 是 SSE 协议头）。
2. 建 `ChatTurnRun` + `supervisor.register` + `attach_task`（背景执行立即启动），路由立即返回 SSE 订阅流。
3. 背景 task（`_execute_new_turn`）：发 `session_created/message_start` → `ContextManager` 组装 + 必要时压缩（`compaction_started/done`）→ `ReActRunner.from_profile` 跑 `ReActAgent.stream` 透传事件 → `TurnFinalizer` 落 DB + 发 `done/error/partial`。

特点：

- **执行与传输解耦**：agent 跑在独立 task，客户端断开 / 浏览器关闭 / SSE 链路死都不影响落库。
- **断线重连**：`run.subscribe(last_seq=N)` 先回放 `events.jsonl`（seq>last_seq）再接 broadcaster 实时；`baseline_seq` 去重防 resume 翻倍。
- **中断**：`/chat/stop` → `run.abort()` → `abort_event.set()` → agent break → finalizer 落 aborted。
- **续写**：`/chat/resume` 直接接入活跃 run 或启新 resume turn（`_execute_resume`，含 `ResumeStreamDedup` 流式去重）。
- 工具集受 profile `tools_allowed` 限制，chat 默认只读（`knowledge_search`/`time_tool`/`read_message`）。

---

## 6. 对外 LLM 网关端点（CLI / 第三方直连）

主入口：`POST /api/v1/llm/chat/completions`（`api/routes/v1/llm.py`）。

- **鉴权**：`X-API-Key` header（`ApiKeyUser` 依赖，`user_api_keys` 表存 SHA256 哈希）；配额/预算/限流/缓存/审计由 LLMGateway Pre/Post 中间件按 user_id 自动生效。
- **请求体**：OpenAI Chat Completions 兼容子集（`messages`/`tools`/`tool_choice`/`stream`/`temperature`/`max_tokens`/`extra_options`/`idempotency_key`）。
- **model 三形态**：空 → 系统默认链；`fast|smart|strong` → 档位链（跨 provider fallback）；`provider:model` → 显式 pin（对话链，同 provider 后备）。
- **响应**：非流式返回 chat.completion JSON（扩展字段 `forge.provider/cache_hit/cost_usd/fallback_position`）；流式 SSE 输出 chat.completion.chunk + `data: [DONE]`，带 tools 时工具调用在最终块以完整 `delta.tool_calls` 一次性下发（网关层已聚合）。

**未来 CLI 客户端的设计约定**（尚未开工）：

- 胖客户端形态：agent loop（ReActAgent + lifecycle 组合）在用户本机运行，工具直接操作本地文件系统；
- Plan Mode / Workflow / HITL 都是客户端本地行为（终端交互确认即可，无需服务端 token 机制）；
- LLM 调用通过本端点直连，享受服务端模型治理（热配链路/fallback/熔断）与成本治理；
- 优先以共享包形式复用 `forge.agents` 内核（其依赖干净：仅 `forge.core.types` / `forge.llm.contracts` / `forge.tools` / `forge.prompts` / `forge.observability`，零 FastAPI/DB 依赖）。

---

## 7. 运行时安全边界

- **工具白名单**：chat profile 的 `tools_allowed` 限定可见工具，启动期校验工具均已注册。
- **工具 guardrail**：`ToolExecutor` 的 access→permission→rate_limit→dangerous_op 流水线 + workspace 路径策略；危险工具进 `audit.jsonl`。
- **Chat 侧**：profile 限制为只读/低风险工具。
- **对外 LLM 端点**：API Key 鉴权（吊销/过期/用户禁用检查）+ user 级配额/预算/入站限流。
- **守护兜底**：LoopGuard（步数/死循环/token/墙钟）经 `GuardLifecycleAdapter` 对 agent 执行生效。

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
- **部分未对齐**：前端 `agentsApi` 与页面入口存在，但后端未挂 `/agents`。

---

## 9. 当前版本结论（架构画像）

一句话总结：

- Forge 当前是**「单一 ReActAgent 内核 + 可组合 AgentLifecycle 扩展」**的智能体框架，服务端专注 **Chat（Web）** 执行路径，并以 **OpenAI 兼容网关端点** 对外开放 LLM 调用能力（供未来 CLI 胖客户端直连）。

工程成熟度：

- **后端**：内核 + 扩展机制 + Chat 主链 + 网关对外端点已成闭环，可跑完整生命周期。
- **前端**：聊天链路成熟；管理可视化仍需补齐。
- **历史包袱**：旧 CLI 执行路径 / Adaptive / orchestration 模块已清理。

---

## 10. 推荐阅读路径（新人上手）

详见 [docs/learning_path.md](docs/learning_path.md)（含 `file:line` 跳转与自检标准）。速览：

1. `api/routes/router.py` + `api/lifespan.py`（路由全景 + 启动装配）
2. `agents/react/agent.py` `stream()`（内核：一个 step 怎么跑）
3. `agents/lifecycle.py` `MultiLifecycle`（扩展机制：架构的钥匙）
4. `chat/orchestrator.py` → `chat/turn_run.py` → `chat/supervisor.py`（Chat 主链）
5. `tools/base.py` + `llm/gateway.py` + `llm/binding.py`（能力层）
6. `api/routes/v1/llm.py`（对外 LLM 网关端点：CLI 接入面）
7. `agents/profiles.py` + `config/sys_config.dev.yaml` 的 `agent_profiles`（配置即治理）
