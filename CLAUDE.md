# Forge
所有代码注释 日志打印都必须是中文为主 必须以简体中文回复我

## 1. 项目定位

Forge 的核心目标是：

- 输入自然语言目标；
- 经由规划、执行、合并、验证等步骤；
- 产出可落地的结构化成果（代码补丁、报告、事件流、运行产物）。

当前代码实现可概括为“两条主路径并行存在”：

- **Chat 路径（稳定）**：`/api/v1/chat/*`，基于 `TurnOrchestrator` 的流式对话与工具调用。
- **Adaptive 路径（新主线）**：`/api/v1/runs*` + `/api/v1/artifacts*`，基于 `AdaptiveRun` 的任务图执行流程。

---

## 2. 顶层目录与职责

```text
Forge/
├── server/  # FastAPI 后端（核心业务）
├── web/     # React 前端（对话与管理界面）
├── cli/     # CLI 目录（当前为空，尚未落地）
├── docs/    # 设计文档与方案说明
└── *.md     # 项目说明与学习文档
```

关键观察：

- `server/` 是当前唯一完整可运行的业务核心。
- `web/` 已具备完整路由与对话能力，但部分页面能力与后端接口尚未完全对齐。
- `cli/` 目录当前为空，CLI 仍处于规划/未实现状态。

---

## 3. 后端架构（`server/src/forge`）

### 3.1 分层结构

- `api/`：FastAPI 路由、依赖注入、中间件、请求/响应 schema。
- `chat/`：聊天回合编排（准备上下文、执行 Agent、收尾入库、SSE 事件）。
- `adaptive/`：任务图运行时（Discover/Plan/Validate/Execute/Integrate/Verify/Close）。
- `agents/`：ReActAgent 与执行模式抽象。
- `llm/`：LLMGateway、路由/调度链、Provider 注册与客户端池。
- `tools/`：工具基类、注册中心、工具执行。
- `infrastructure/`：数据库、缓存、队列、事件总线等基础设施。
- `config/`：配置加载与域模型（`sys_config.*.yaml` 映射）。

### 3.2 应用启动与运行时装配

应用入口：`server/src/forge/api/server.py`

- 创建 FastAPI 应用，注册 CORS、`ClientTypeMiddleware`、Tracing、ErrorHandler。
- 挂载 `api_router` 到 `/api`，即业务前缀为 `/api/v1/*`。
- `lifespan` 负责启动时装配：日志、DB、Redis、模型缓存、LLM 池与网关、RAG 组件等。

配置入口：`server/src/forge/config/settings.py`

- 配置优先级：`init_settings(path)` > `APP_CONFIG` > `APP_ENV` > 默认配置路径。
- `task_execution` 配置定义了 Adaptive 的工具白名单、模型档位、硬上限与默认选项。

### 3.3 API 功能域总览（v1）

聚合入口：`server/src/forge/api/routes/router.py`

当前后端功能域不是单一 chat 服务，而是以下 10 个业务域并行：

- **系统与健康**：`/system/health`、`/system/ready`
- **认证与会话身份**：`/auth/login`、`/auth/refresh`、`/auth/me`、`/auth/change-password`
- **用户管理（管理员）**：`/users`、`/users/{id}`、`/users/{id}/reset-password`
- **API Key 管理**：`/api-keys`（创建、列表、吊销）
- **聊天会话**：`/sessions`、`/sessions/{id}/messages`
- **流式对话**：`/chat/completions`、`/chat/resume`、`/chat/stop`、`/chat/regenerate`、`/chat/quota`
- **Adaptive 运行编排**：`/runs`、`/runs/{id}/events`、`/runs/{id}/abort`、`/runs/{id}/decide`
- **Adaptive 产物查询**：`/artifacts`、`/artifacts/{id}`
- **知识库（KB）域**：`/kb`、`/kb/{id}/documents`（上传/查询/删除）
- **模型与供应商治理**：`/models`、`/providers`、`/admin/events`

说明：

- 旧 `workflow` 模块仍在 `server/src/forge/orchestration/workflow`，但 v1 路由未挂载 `workflow` 端点。
- `documents.py`、`retrieval.py` 路由文件存在占位代码，当前未暴露有效接口。

### 3.4 功能分层调用链（Route -> Service -> Repo -> ORM）

后端主流实现遵循“四层链路”：

1. **Route 层**（`api/routes/v1/*.py`）  
负责协议适配、参数校验、权限依赖注入、SSE 输出。
2. **Service 层**（`api/services/*.py`）  
负责业务编排与跨仓储聚合，例如 `AuthService`、`SessionService`、`KbService`、`AdminModelService`。
3. **Repository 层**（`infrastructure/database/repositories/*`）  
负责 ORM 查询与事务内持久化细节。
4. **ORM 层**（`infrastructure/database/orm/*`）  
负责表结构映射与实体字段。

典型链路示例：

- `chat/completions`：Route -> `TurnOrchestrator`（chat 编排）-> DB Repo + LLMGateway + ToolExecutor
- `kb/{id}/documents` 上传：Route -> `KbService.upload_document` -> `KbIngestService.ingest` -> Repo/向量库/BM25
- `/models` 管理：Route -> `AdminModelService` -> Provider/Model Repo -> ModelConfigCache + EventBus
- `/runs`：Route -> `RunSupervisor.start_run` -> `AdaptiveRunOrchestrator` -> `AdaptiveRunStore`

### 3.5 数据存储架构

后端当前是“多存储协作”架构：

- **关系库（MySQL/SQLite）**：核心业务元数据
- **Redis（可选但默认接入）**：模型配置缓存、限流、幂等与精确缓存增强
- **本地文件系统**：上传文档与 Adaptive 运行态文件
- **向量库 + BM25 库**：知识检索索引

关系库核心表（ORM 映射）：

- 用户与鉴权：`users`、`refresh_token_blacklist`、`user_api_keys`
- 对话域：`chat_sessions`、`chat_messages`、`session_summaries`
- 知识库域：`knowledge_bases`、`kb_documents`、`kb_document_chunks`
- 模型治理域：`providers`、`provider_keys`、`models`

Adaptive 运行态不走关系库，采用文件落盘：

- `state.json`（run 快照）
- `events.jsonl`（事件流）
- `artifacts/*.json`（产物）

### 3.6 LLM 子系统（Gateway 化）

`llm/` 不是简单 SDK 封装，而是网关化架构：

- 统一入口：`LLMGateway`
- 请求流程：Pre 中间件 -> Dispatcher/Router -> Provider -> Post 中间件
- 能力组件：限流、预算、去重、缓存、审计、熔断、舱壁、fallback
- 供应商适配：通过 registry 动态注册（openai/anthropic/google/mock 等）

这使 chat/adaptive/memory 等子系统都共享一套可观测与可治理的 LLM 调用能力。

### 3.7 RAG 与知识库子系统

RAG 由 `lifespan` 在启动阶段动态装配，具备“软降级”能力：

- 解析：`retrieval/parsers/*`（pdf/word/text）
- 切分：`retrieval/chunkers/*`
- 向量化：`retrieval/embedders/*`
- 存储：`retrieval/stores/vector/*` + `retrieval/stores/bm25/*`
- 检索：`RetrieverFactory` + `knowledge_search` 工具调用

文档入库由 `KbIngestService` 以 Saga 风格编排：  
`parsing -> chunking -> embedding -> persist -> indexed`，失败时执行补偿清理并标记 `failed`。

### 3.8 后台任务、事件与并发机制

- **RunSupervisor**：Adaptive run 的进程内后台调度与取消/恢复控制
- **TaskQueue**：默认 `LocalTaskQueue`，可切 Celery，失败可降级到 `NullTaskQueue`
- **EventBus**：默认进程内总线，用于模型配置变更等事件通知
- **SSE**：chat/run/admin 三类事件流都基于 SSE 输出

该设计适合单机/单进程先跑通全链路，再向多进程/分布式演进。

### 3.9 当前实现差异与缺口（Server 视角）

- `mode_router` / `chat mode` 三路路由尚未在 chat 入参层实现，chat 与 adaptive 仍是分离入口。
- `/kb` 是当前真实知识库前缀；前端中仍有 `knowledge-bases` 形态的调用约定，需要继续对齐。
- `agents` 相关前端入口已存在，但后端未挂载 `/agents` 业务路由。
- `workflow` 旧模块保留但未暴露 API，属于迁移残留。

### 3.10 上下文管理系统（Context）

后端当前是“**双层上下文架构**”：

- **兼容入口层**：`forge.context`（当前 chat 主链实际调用）
- **新一代内核层**：`forge.context_mgmt`（已落地实现，并通过适配器承接）

关键关系：

- `chat/assembler.py` 调用 `forge.context.factory.build_context_builder()`
- `forge.context.builder.CompositeContextBuilder` 已被替换为
  `context_mgmt.compat.CompositeContextBuilderAdapter`
- 也就是说，chat 链路表面走老接口，内核已经是新 `context_mgmt` 构建引擎

#### 3.10.1 统一数据模型

`context_mgmt/types.py` 定义了统一值对象：

- `ContextRequest`：统一入参（chat/task/workflow）
- `WindowBudget`：system/dialogue/tool_result 三分区预算
- `ContextSnapshot`：统一出参（messages + usage + degraded）
- `ContextUsage/LayerUsage`：可观测分层用量（system/workspace/facts/summary/dialogue/current_input）
- `CompactionResult`：压缩结果对象

#### 3.10.2 构建流水线（Fork-Join）

`DefaultContextBuilder` 的执行模型：

1. `BudgetPolicy.allocate()` 分配预算
2. 并行执行：
   - `PromptRenderer.render()`
   - `ContentGatherer.gather()`
3. `MessageAssembler.assemble()` 串行拼接最终 messages
4. 输出 `ContextSnapshot` 并带全量用量与降级信息

`ContentGatherer` 采用 `asyncio.gather(return_exceptions=True)`：

- summary/facts 类 provider 失败会进入 `degraded`（软降级）
- history provider 属核心路径，异常向上抛出（硬失败）

#### 3.10.3 Provider/Filter/Policy 扩展点

新系统把“上下文构建”拆成可替换插件：

- `ContentProvider`：history/summary/facts/workspace/workflow_step
- `HistoryFilter`：recent/semantic/hybrid/null/step_scoped
- `ToolResultPolicy`：verbatim/truncating/evicting/summarizing
- `BudgetPolicy`：按 mode 分配预算
- `TokenMeter`：统一 token 计量

按 `ContextMode` 的默认策略：

- `CHAT`：`HybridFilter + TruncatingPolicy`
- `TASK`：`NullFilter + EvictingPolicy`
- `WORKFLOW`：`StepScopedFilter + SummarizingPolicy(当前降级到 truncate)`

注意：compat 适配器为了零退化，chat 现状默认仍是
`RecentFilter + VerbatimPolicy` 的旧行为语义。

### 3.11 上下文压缩子系统（Compaction）

当前后端有两条压缩链路并存：

- **在用链路（chat 主链）**：`chat.ContextAssembler` 主动压缩
- **新链路（context_mgmt）**：`CompactionController + Trigger + Strategy`

#### 3.11.1 在用链路（ContextAssembler）

压缩触发条件（`should_compact`）：

- `history_messages_dropped > 0`
- 或 `estimated_input_tokens / context_window > threshold(默认 0.85)`

触发后执行：

1. 调 `SummaryService.summarize_session()`
2. 成功后重建 context（reassemble）
3. 写入 `compaction_performed / compaction_token_saved / rebuild_count`
4. 发 SSE 事件：`compaction_started` / `compaction_done`

失败语义：

- 摘要服务失败不终止主流程，回退到压缩前上下文，并写 `degraded=compaction_failed`

#### 3.11.2 新链路（context_mgmt）

`CompactionController` 把“触发判断”和“执行策略”彻底解耦：

- Trigger：`ThresholdTrigger` / `ExplicitTrigger` / `CompositeTrigger`
- Strategy：`SummaryCompaction` / `NullCompaction`（预留 hybrid/selective_drop）

`SummaryCompaction` 内部同样复用 `SummaryService`，语义保持一致。

现状判断：

- 目前 chat 主流程直接调用的是 `ContextAssembler` 压缩
- `ContextManager` 入口已具备完整能力，但尚未成为 chat 唯一入口

### 3.12 Memory 记忆系统

memory 子系统已形成独立架构，包含“读路径 + 写路径（事件驱动）”。

#### 3.12.1 读路径（同步）

Context 构建阶段通过 `MemoryStore` 协议读取：

- `get_summary(session_id)`：会话摘要
- `recall_facts(query)`：长期事实召回

当前实现：

- `CompositeMemoryStore` 已接入 `SummaryStore`
- `FactStore` 仍为 Stage 2 占位，`recall_facts` 默认返回空列表
- 关闭 memory 时走 `NullMemoryStore`

#### 3.12.2 写路径（异步事件驱动）

写路径链路：

1. `TurnFinalizer` 在对话成功结束后 publish `turn.completed`
2. `memory.hooks.install_memory_hooks()` 订阅事件
3. 满足 `every_n_turns` 阈值后派发 `memory.summarize` 任务
4. 任务调用 `SummaryService`：
   - 读取近 N 条消息
   - 调 `Summarizer` 走 LLMGateway 生成摘要
   - `SummaryStore.upsert` 写入 `session_summaries`

这条链路把 chat 请求与摘要写入彻底解耦，避免拉长对话时延。

#### 3.12.3 Summary 持久化模型

`SummaryStore` 关键特征：

- MySQL `ON DUPLICATE KEY UPDATE` 原子 upsert
- `version` 自增
- `workspace_id` 可选维度
- 失败抛 `MemoryStoreError`，由上层决定重试或降级

#### 3.12.4 Memory 策略层（已定义，部分待实现）

策略接口已就位：

- `ConflictResolver`（Insert/Replace/Merge/Skip）
- `ForgettingPolicy`（is_alive / should_prune）
- `MemoryScope`（user/workspace/tenant 隔离边界）

当前默认仍是 NoOp 策略，属于可演进点。

### 3.13 存储系统（Storage Architecture）

后端存储不是单一数据库，而是“分层多存储”：

#### 3.13.1 结构化元数据存储

- 主存储：MySQL（dev 配置也可切 SQLite）
- ORM 表覆盖：用户、鉴权、会话消息、KB 元数据、模型治理、摘要
- 访问路径：Route -> Service -> Repository -> ORM

#### 3.13.2 运行态日志型存储（JSONL）

核心 JSONL 原语：`infrastructure/jsonl.py`（append/iter/tail/iter_after + 原子写）

应用于：

- `cost.jsonl`：LLM 成本流水
- `audit.jsonl`：危险工具审计
- `adaptive_runs/*/events.jsonl`：run 事件流
- `adaptive_runs/*/state.json` 与 `artifacts/*.json`：run 状态与产物

优势：

- 追加写性能好
- 可恢复性强
- 与单机架构天然契合

#### 3.13.3 文件对象存储

`FileStorage` 抽象定义对象文件生命周期：

- `LocalFileStorage` 为当前主实现（`{kb_id}/{doc_id}/{filename}`）
- `S3` 实现文件已存在，作为可选后端路径
- DB 中只存 `storage_path` 键，不存绝对路径，便于迁移

#### 3.13.4 检索索引存储

- 向量层：`ChildVectorStore`（默认 Chroma 实现）
- 倒排层：`BM25Store`（默认 sqlite_fts5 实现）
- 由 `RetrieverFactory` 组装成 `ParentChildRetriever`
- 通过 `retrieval.runtime` 全局注册给 `knowledge_search` 工具使用

#### 3.13.5 成本与审计存储

- `CostTracker` 进程内累计 + 周期 flush 到 `cost.jsonl`
- `AuditLog` 记录危险工具调用（allowed/executed/blocked/failed）
- 预算检查依赖 `(baseline + in-memory delta)`，属于最终一致模型

### 3.14 可观测性设计（Observability）

可观测性由 4 条链路构成：日志、追踪、指标、成本/预算。

#### 3.14.1 日志（Logging）

- 统一入口：`observability.logging.setup_logging`
- 自动注入上下文字段：`trace_id/user_id/client_type`
- 支持彩色文本与 JSON 双格式
- 第三方高噪声 logger 抑制（如 `jieba/httpx` debug）

#### 3.14.2 追踪（Tracing）

- 统一 API：`with span("name") as s: s.set(...)`
- exporter 可切：`none / otel / langfuse`
- chat 核心阶段均已埋点：prepare/assemble/compact/finalize/resume
- middleware 级 `X-Request-ID` 贯穿请求链路

#### 3.14.3 指标（Metrics）

已落地重点在 LLM 指标：

- 请求量、延迟、token、成本、熔断状态、预算超限计数
- Prometheus 软依赖（未安装时 no-op 不影响业务）

当前状态说明：

- `business_metrics.py`、`technical_metrics.py`、`cost_metrics.py`
  仍是占位模块，尚未形成完整业务指标体系。

#### 3.14.4 成本与预算观测

- `CostTracker` 记录 provider/model/user 维度成本
- `BudgetConfig` 支持全局、默认用户、指定用户三级限额
- 与 `quota` 滚动窗口系统联动（5小时/7天）
- 超限抛 `LLMBudgetExceeded`，调用链可提前阻断

#### 3.14.5 事件与SSE可观测

- chat 事件流：delta/tool_call/reasoning/compaction/done/partial
- adaptive 事件流：run/task/wave/plan/integration/verify
- admin 事件流：模型配置变更通知

这使前端和 CLI 可以实现“过程可见、失败可定位”的交互体验。

---

## 4. Chat 路径（对话主链）

主入口：`POST /api/v1/chat/completions`

核心流程（`forge.chat.orchestrator.TurnOrchestrator`）：

1. `TurnPreparer`：准备会话、用户消息、assistant 占位消息。
2. 发送生命周期事件：`session_created`、`session_renamed`、`message_start`。
3. `ContextAssembler`：组装上下文，必要时触发压缩（`compaction_started/done`）。
4. 组装 `GatewayLLMAdapter` + Runner（当前默认 `react`）。
5. 流式透传中间事件：`delta/tool_call/tool_result/citations/reasoning_delta`。
6. `TurnFinalizer`：落库并输出 `done/error/task_partial`。

特点：

- SSE 协议严格对齐前端事件模型。
- `/chat/stop` 通过内存中的 active stream 中断当前生成。
- 工具集受 `chat_tool_allowlist` 限制，默认只开放查询类工具。

---

## 5. Adaptive 路径（任务运行主链）

主入口：`POST /api/v1/runs`

- 接收 `goal + task_options`，创建 `AdaptiveRun`，立即交给 `RunSupervisor` 后台执行。
- 事件流由 `/runs/{id}/events?follow=true` SSE 持续订阅。

### 5.1 核心对象

- `AdaptiveRun`：运行实体，含状态、任务图、产物、元数据、options 快照。
- `TaskGraph/TaskNode`：DAG 任务图与节点定义（kind/tools/scope/deps/model_profile 等）。
- `Artifact`：运行产物（`task_graph/patch_set/integration_report/final_report` 等）。

### 5.2 状态机

`created -> planning -> validating -> executing -> integrating -> verifying -> completed`

可转终态：`failed / blocked / aborted`。

### 5.3 执行阶段拆解

1. **Discover**：生成 discovery report（可用真实 DiscoveryAgent 或 fallback）。
2. **Plan**：Planner 产出 TaskGraph（可由 LLM tool-use 结构化输出）。
3. **Validate**：校验 DAG、tool allowlist、scope 越界、读写约束、max_steps 等。
4. **Execute**：按 wave 调度并发执行；写任务可进入 git worktree 隔离。
5. **Integrate**：合并 PatchSet，先 dry-run `git apply --check`，后原子 apply。
6. **Verify**：执行验证命令（`verifier_cmd` 或节点 command），失败可触发修复循环。
7. **Close**：写入最终报告与完成事件。

### 5.4 并发与隔离

- 调度器按依赖层级计算 wave，并拆分同 wave 的写冲突任务。
- `writer_mode=isolated_worktree` 时，写任务在 `/tmp/forge-...` worktree 执行并回收。
- Integrator 基于 diff 合并，冲突则产出 `conflict_report` 并将 run 置为 `blocked`。

### 5.5 持久化与可观测性

`AdaptiveRunStore` 目录结构（按 workspace 维度）：

- `state.json`：run 快照
- `events.jsonl`：事件流
- `artifacts/*.json`：产物

并有全局 `run_index`（`adaptive_runs_index.jsonl`）用于 run_id 反查 workspace。

---

## 6. 前端架构（`web/`）

### 6.1 技术栈

- React 18 + TypeScript + Vite
- React Router v6
- TanStack Query
- Zustand（认证状态）
- Axios + fetch ReadableStream SSE 客户端

### 6.2 分层

- `src/api/`：REST 客户端与 SSE 封装（统一鉴权、401 自动 refresh）。
- `src/pages/`：路由页面（聊天、知识库、设置、管理）。
- `src/hooks/useChatStream.ts`：对话流式状态机（send/abort/resume）。
- `src/store/auth.ts`：用户状态持久化与 token 内存策略。
- `src/types/index.ts`：后端契约类型（含 Chat 与 Adaptive Run 事件）。

### 6.3 前后端对齐情况（当前实现）

- **已对齐**：Chat SSE 事件与 `/chat/*` 接口、会话与消息接口。
- **部分未对齐**：前端存在 `agentsApi` 与对应页面入口，但后端当前路由未挂 `/agents`。
- **Adaptive UI 状态**：前端类型中已定义 Run/Artifact/SSE 结构，但页面层尚未看到完整 Runs 视图实现。

---

## 7. 运行时安全边界

Adaptive 侧通过多层约束降低误写风险：

- 工具 allowlist：Planner 声明的工具必须在白名单。
- `write_scope` 越界校验：必须位于 workspace root 下。
- READ 任务禁写：禁止写 scope 与写/副作用工具。
- `allow_write=false`：从 Validator 到 Executor 双重拒绝写任务。
- 写隔离：默认走 `git worktree`，而非直接污染主工作区。

Chat 侧通过 `chat_tool_allowlist` 将对话工具限制在只读/低风险能力。

---

## 8. 当前版本结论（架构画像）

一句话总结：

- Forge 当前是**“稳定 Chat 主链 + 正在增强的 Adaptive 任务链”**双轨架构。

从工程成熟度看：

- **后端**：Adaptive 主体（状态机、调度、隔离、集成、验证）已形成闭环，可跑完整生命周期。
- **前端**：聊天链路成熟；Adaptive 的类型与事件协议已准备，但完整 Runs 可视化仍需补齐。
- **CLI**：目录存在但尚未落地，M10 目标尚未在仓库实现。

---

## 9. 推荐阅读路径（新人上手）

1. `server/src/forge/api/server.py` + `api/lifespan.py`（理解启动与依赖装配）
2. `server/src/forge/chat/orchestrator.py`（理解对话链路）
3. `server/src/forge/adaptive/orchestrator.py`（理解任务主流程）
4. `server/src/forge/adaptive/executor.py` + `workspace.py` + `integrator.py`（理解执行隔离与合并）
5. `web/src/pages/chat/ChatPage.tsx` + `hooks/useChatStream.ts`（理解前端流式消费）
