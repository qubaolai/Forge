# Forge 后端架构设计

> 本文基于 `server/src/forge` 当前**真实代码**编写（非历史方案）。
> 重要：旧版 `adaptive/`（DAG / Validator / Wave 调度）已被删除，CLAUDE.md 中关于 Adaptive 路径的描述已过时，请以本文为准。
> 所有 `file:line` 引用均可点击跳转到源码。

---

## 0. 一句话总览

Forge 后端是一个**以 ReAct Agent 为内核、以可组合 AgentLifecycle 为唯一扩展机制**的智能体执行框架。

它的核心设计判断只有一句话：

> **所有模式（mode）差异——chat / plan_exec / workflow——都被外置成 `AgentLifecycle` 的不同组合，绝不在 `ReActAgent` 内部用 `if mode == ...` 分支。**

理解了这句话，就理解了整个后端的解耦骨架。

---

## 1. 两条执行路径（最顶层的分工）

后端并存两条平行的执行路径，它们**共享同一个 `ReActAgent` 内核**，只在「编排层 + 持久化层 + lifecycle 组合」上不同。

| 维度 | Chat 路径（Web 端） | CLI 路径（plan_exec / workflow） |
|------|--------------------|----------------------------------|
| 入口路由 | `POST /api/v1/chat/*` | `POST /api/v1/runs` + `/v1/decisions/*` |
| 顶层编排 | `TurnOrchestrator` | `RunOrchestrator` |
| 运行容器 | `ChatTurnRun`（背景 asyncio.Task） | `RunOrchestrator` 自身的背景 task |
| 持久化 | `chat_messages` 数据库 + `events.jsonl` | `RunStore`（纯 JSONL，不入库） |
| 默认工具集 | 只读（`knowledge_search` / `time_tool`） | 读写全集（Plan Mode 动态切换） |
| lifecycle 组合 | Guards 兜底 | Guards + PlanMode + Workflow + Persistence |
| 人机交互（HITL） | 无 | 有（Plan 批准 / Workflow gate） |

- Chat 编排：[orchestrator.py:64](server/src/forge/chat/orchestrator.py:64) `TurnOrchestrator`
- CLI 编排：[run_orchestrator.py:47](server/src/forge/agents/run_orchestrator.py:47) `RunOrchestrator`
- 共享内核：[agent.py:92](server/src/forge/agents/react/agent.py:92) `ReActAgent`

```
                        ┌─────────────────────────┐
   Web 端  ── /chat ──▶ │   TurnOrchestrator      │──┐
                        └─────────────────────────┘  │
                                                      │   都构造
                        ┌─────────────────────────┐  │   ReActAgent.stream(lifecycle=...)
   CLI 端  ── /runs ──▶ │   RunOrchestrator       │──┤
                        └─────────────────────────┘  │
                                                      ▼
                                        ┌──────────────────────────┐
                                        │   ReActAgent (内核)       │
                                        │   纯 ReAct 循环, 不含 mode │
                                        └──────────────────────────┘
                                                      ▲
                                  注入不同的 AgentLifecycle 组合 ──┘
```

---

## 2. 核心内核：ReActAgent

`ReActAgent` 是经典 ReAct（Reason + Act）循环，但用 **LLM 原生 function calling**，不做 "Thought/Action/Observation" 文本解析（工业实现更稳定）。

主流式入口：[agent.py:188](server/src/forge/agents/react/agent.py:188) `ReActAgent.stream(...)`

单步（step）执行序列（这就是所有扩展点的「挂载坐标系」）：

```
on_start                         一次性     agent.py:231
└─ for step in range(max_steps):
     resolve_tools               每步       agent.py:264   ← 动态工具集
     before_step                 每步       agent.py:274   ← 注入引导 / 强制纯文本
     [LLM 流式调用]                          agent.py:301
     if 无 tool_calls: break(终态)           agent.py:434
     for tc in tool_calls:
        before_tool_call         每工具     agent.py:635   ← veto 拦截 (Plan/Workflow)
        [执行 或 用 veto 替代]    agent.py:651
        on_tool_result           每工具     agent.py:673   ← 大产物落 artifact
     after_step                  每步       agent.py:550   ← checkpoint 持久化
on_complete / on_error           终态       agent.py:564 / agent.py:599
```

关键设计点：

- **`lifecycle=None` ⇒ 纯 ReAct**。所有 hook 调用前都判空，没有 lifecycle 时行为完全等价于原始 ReAct（[agent.py:231](server/src/forge/agents/react/agent.py:231) 等处的 `if lifecycle is not None`）。
- **工具并行调度**：同一 step 内连续的 `parallelism_safe=True` 工具会被收集成 batch 用 `asyncio.gather` 并行，unsafe 工具严格串行，整体保留 LLM 给的顺序。见 [agent.py:468](server/src/forge/agents/react/agent.py:468) 的批处理循环 + [base.py:49](server/src/forge/tools/base.py:49) 的 `parallelism_safe`。
- **abort 优雅中断**：每步、每个 chunk、每个工具派发前都检查 `abort_event`，已在 flight 的工具不强行打断（避免副作用），未派发的补 `aborted` 的 tool_result（[agent.py:472](server/src/forge/agents/react/agent.py:472)）。
- **可观测性内建**：每层都有 `span(...)` 埋点（stream / step / llm_call / tool 四级）。

---

## 3. 第一扩展点（最核心）：AgentLifecycle

> 这是整个架构的「主扩展点」。其他扩展点都是围绕它服务的。

定义文件：[lifecycle.py](server/src/forge/agents/lifecycle.py)

### 3.1 协议（Protocol）

[lifecycle.py:120](server/src/forge/agents/lifecycle.py:120) `class AgentLifecycle(Protocol)`，8 个 hook，**全部默认 no-op**，实现方按需覆写、无需继承：

| Hook | 签名 | 返回语义 | 典型用途 |
|------|------|---------|---------|
| `on_start` | `(RunContext)` | — | 初始化（Plan Mode 置位 / 写 run_started 事件） |
| `resolve_tools` | `(StepContext) -> list[dict] \| None` | None=用默认；list=替换本步 schema | **动态工具集**（Plan/Exec 切换） |
| `before_step` | `(StepContext) -> StepDecision \| None` | 注入 system / 强制纯文本 | LoopGuard 引导 / 收尾 |
| `before_tool_call` | `(ToolCall, StepContext) -> ToolCallVeto \| None` | None=放行；veto=替代执行 | **HITL 拦截**（exit_plan_mode / advance_phase） |
| `on_tool_result` | `(ToolCall, Message) -> Message \| None` | None=不改；Message=替换 | 大产物落 artifact 回灌占位 |
| `after_step` | `(StepContext, StepOutcome)` | — | checkpoint 持久化 |
| `on_complete` | `(RunResult)` | — | 终态落库 completed |
| `on_error` | `(BaseException, RunResult)` | — | 终态落库 failed |

共享数据类型（全部 frozen dataclass，不可变快照）：`RunContext` / `StepContext` / `StepDecision` / `StepOutcome` / `ToolCallVeto` / `RunResult`（[lifecycle.py:44-114](server/src/forge/agents/lifecycle.py:44)）。

### 3.2 组合器：MultiLifecycle（解耦的关键）

[lifecycle.py:193](server/src/forge/agents/lifecycle.py:193) `class MultiLifecycle`。把多个 lifecycle 串成一个，按 hook 语义采用三种合并策略：

| 合并策略 | 适用 hook | 语义 |
|---------|----------|------|
| **首个非 None 胜出** | `resolve_tools` / `before_step` / `before_tool_call` | 找到第一个返回非 None 就返回，避免互相覆盖工具集 / 拦截语义混乱 |
| **pipeline 累计** | `on_tool_result` | 依次调用，后者基于前者结果（[lifecycle.py:267](server/src/forge/agents/lifecycle.py:267)） |
| **全部都调** | `on_start` / `after_step` / `on_complete` / `on_error` | 各自副作用，逐个执行 |

**单 lifecycle 异常被隔离**：每个 hook 调用都包 try/except 打日志，一个 lifecycle 挂掉不影响其他 lifecycle 和主流程（贯穿 [lifecycle.py:216-293](server/src/forge/agents/lifecycle.py:216)）。

### 3.3 这套机制如何实现「mode 差异外置」

三种模式 = 三种 lifecycle 组合，由编排层装配（**这是全部的 mode 路由逻辑**）：

- Chat：`[GuardLifecycleAdapter]`（仅兜底守护）— [runner.py:112](server/src/forge/chat/runner.py:112)
- plan_exec：`[Guards, PlanModeLifecycle, RunStorePersistenceLifecycle]` — [run_orchestrator.py:196](server/src/forge/agents/run_orchestrator.py:196) `_build_lifecycles`
- workflow：`[Guards, WorkflowLifecycle, RunStorePersistenceLifecycle]` — 同上，按 profile flag 选装

`ReActAgent` 对此**完全无感知**——它只看到一个 `AgentLifecycle`。

---

## 4. 围绕 AgentLifecycle 的具体实现组件

### 4.1 LoopGuard → GuardLifecycleAdapter（守护体系的收编）

旧的 LoopGuard 体系（死循环 / 步数 / token / 墙钟守护）通过一个适配器无侵入接入 lifecycle 协议：

- 协议与守护实现：[guards/base.py](server/src/forge/chat/guards/base.py)，四个内置守护 `StepSafetyNet` / `StuckDetector` / `TokenBudgetGuard` / `WallClockGuard`
- 适配器：[lifecycle_adapter.py:26](server/src/forge/chat/guards/lifecycle_adapter.py:26) `GuardLifecycleAdapter`，只做 `StepContext → LoopState` 字段映射 + 合并多个 guidance 到 `before_step`（[lifecycle_adapter.py:38](server/src/forge/chat/guards/lifecycle_adapter.py:38)）

设计价值：守护逻辑无需重写就接入了新协议，**新旧体系桥接**的范例。

### 4.2 PlanModeLifecycle（Claude Code 风格的 Plan-Exec）

[plan_mode.py:52](server/src/forge/agents/plan_mode.py:52) `class PlanModeLifecycle`。三个机制叠加：

1. **动态工具集**：`PLAN_MODE` 是一个 `ContextVar`（[plan_mode.py:41](server/src/forge/agents/plan_mode.py:41)）。`resolve_tools` 据其返回不同 schema：Plan 阶段只读 + `exit_plan_mode`，Exec 阶段解锁写工具（[plan_mode.py:94](server/src/forge/agents/plan_mode.py:94)）。
2. **物理隔离而非自律**：LLM 在 Plan 阶段**物理上看不到写工具的 schema**，不会浪费 token 反复试错被锁的工具。
3. **HITL gate**：`before_tool_call` 拦截 `exit_plan_mode`，创建 `PendingDecision`，`await event.wait()` 阻塞主流程等用户批准；批准则 `PLAN_MODE.set(False)` 解锁，否则把反馈回灌让 LLM 调整计划（[plan_mode.py:97-196](server/src/forge/agents/plan_mode.py:97)）。

关键解耦：**不修改 ToolExecutor**——「锁」是 agent 层语义，与执行器解耦；`exit_plan_mode` 只是个 schema，实际逻辑全在 lifecycle 里。

### 4.3 WorkflowLifecycle（模板驱动的多 phase 流水线）

[workflow_lifecycle.py:86](server/src/forge/agents/workflow_lifecycle.py:86) `class WorkflowLifecycle`。与 Plan Mode 同款 HITL 机制：

- 模板校验：[workflow_lifecycle.py:55](server/src/forge/agents/workflow_lifecycle.py:55) `validate_workflow_template`（phases / gates 形状校验）
- `before_tool_call` 拦截 `advance_phase`：校验 phase 顺序 → 命中 gate 则走 `workflow_gate` HITL → 推进或终止（[workflow_lifecycle.py:154](server/src/forge/agents/workflow_lifecycle.py:154)）
- gate 阻塞：[workflow_lifecycle.py:252](server/src/forge/agents/workflow_lifecycle.py:252) `_await_gate`

### 4.4 RunStorePersistenceLifecycle（CLI 持久化投影）

[persistence_lifecycle.py:45](server/src/forge/agents/persistence_lifecycle.py:45)。把 lifecycle 事件投影到 `RunStore`（JSONL）：

- `after_step` → `step_completed` 事件
- `on_tool_result` → **大产物（超阈值，默认 8KB）落 artifact**，回灌 `[artifact:<id>] {摘要}` 占位，避免大文本撑爆 LLM 上下文（[persistence_lifecycle.py:108](server/src/forge/agents/persistence_lifecycle.py:108)）
- `on_complete` / `on_error` → `transition_status`

**持久化失败不阻断主流程**（所有写操作包 try/except）。

### 4.5 HITL 通用协议（DecisionRegistry）

[hitl.py](server/src/forge/agents/hitl.py)。Plan Mode 与 Workflow 共用的人机交互内核：

- `DecisionRegistry`（[hitl.py:67](server/src/forge/agents/hitl.py:67)）：单进程全局 in-memory 注册表（多进程后接 Redis pub/sub 即可平替）
- `PendingDecision` 自带 `asyncio.Event`，主 agent `await event.wait()`，外部通过 `POST /v1/decisions/{token}` 调 `resolve` 唤醒
- TTL 守护：`cleanup_loop` 后台定期扫表，过期 token 自动 reject（[hitl.py:127](server/src/forge/agents/hitl.py:127)），在 lifespan 中 create_task 运行（[lifespan.py:281](server/src/forge/api/lifespan.py:281)）
- `kind` 三类：`plan` / `workflow_gate` / `tool_confirm`（[hitl.py:29](server/src/forge/agents/hitl.py:29)）

---

## 5. 配置即治理：agent_profiles 扩展点

新增一个 mode **无需写 Python 代码**，只需在 YAML 加一段 profile + 一个 prompt 模板。

- 配置模型：[agent_profiles.py:38](server/src/forge/config/domains/agent_profiles.py:38) `AgentProfile`（pydantic，`extra="forbid"` 严格校验）
- 实际配置：[sys_config.dev.yaml:137](server/config/sys_config.dev.yaml:137) `agent_profiles` 段，定义 chat / plan_exec / workflow 三个 profile
- 加载 + 启动期校验：[profiles.py:26](server/src/forge/agents/profiles.py:26) `load_profiles_at_startup`

启动期 7 项强校验（任一不过直接拒绝启动，[profiles.py:30-104](server/src/forge/agents/profiles.py:30)）：

1. `tools_allowed` / `readonly_tools` 每个工具名在 ToolRegistry 已注册
2. `readonly_tools ⊆ tools_allowed`
3. `sub_agents_allowed` 每个 role 已注册
4. `plan_mode_initial=True ⇒ exit_plan_mode in readonly_tools`
5. `sub_agents_allowed` 非空 ⇔ `spawn_subagent in tools_allowed`
6. `system_prompt_template` 在 PromptRegistry 存在
7. `model_profile` 在 `model_profiles` 字典定义

profile 关键字段：`persistence`（chat_db / run_store / none）、`model_profile`（fast / smart / strong）、`plan_mode_initial`、`requires_template`、`large_artifact_threshold_bytes`。

Runner 据 profile 自动过滤工具：[runner.py:181](server/src/forge/chat/runner.py:181) `ReActRunner.from_profile`。

---

## 6. 其余扩展点

### 6.1 Tool / ToolRegistry（工具扩展点）

- 基类：[base.py:33](server/src/forge/tools/base.py:33) `class Tool(ABC)`。实现 `run`（CPU bound）或 `arun`（IO bound）之一。元数据：`parallelism_safe` / `dangerous` / `required_scope` / `allowed_roles` / `path_role_whitelist`。
- 注册：[registry.py:29](server/src/forge/tools/registry.py:29) `@register_tool` 装饰器，注册期预计算 schema 缓存（[registry.py:41](server/src/forge/tools/registry.py:41)）。
- 执行：`ToolExecutor`（`server/src/forge/tools/executor.py`）带 guardrail 流水线（access → permission → rate_limit → dangerous_op）+ workspace 路径策略。

### 6.2 AgentRole（子 agent 角色扩展点）

[roles/factory.py:8](server/src/forge/agents/roles/factory.py:8) `AgentRole`。7 个内置角色（triage / developer / architect / reviewer / qa / ra / devops），每个声明 `allowed_tools` / `model_preference` / `can_write` / `write_path_prefixes`。

- 内置定义：[factory.py:20](server/src/forge/agents/roles/factory.py:20) `_builtin_roles`
- 动态扩展：[factory.py:206](server/src/forge/agents/roles/factory.py:206) `register_custom_agent_role`（进程级，默认不覆盖内置）

子 agent 通过 `spawn_subagent` 工具派发，受 profile 的 `sub_agents_allowed` 白名单约束。

### 6.3 LLM 网关（Provider / 中间件 / 路由扩展点）

[gateway.py:54](server/src/forge/llm/gateway.py:54) `LLMGateway` 是业务层**唯一对外入口**。调用链：

```
LLMRequest
  → PrePipeline   (validator → rate_limit → budget → dedup → cache)
  → LLMDispatcher (router → chain 遍历 → 熔断 → 重试 → fallback)
  → Provider
  → PostPipeline  (cache_write → dedup_complete → audit)
  → LLMResponse
```

- 中间件可插拔：[gateway.py:76](server/src/forge/llm/gateway.py:76) `from_settings` 接受自定义 Pre/Post 链
- Provider 动态注册：`llm/registry.py` + `llm/providers/`
- 对 agent 的适配：`GatewayLLMAdapter`（[binding.py](server/src/forge/llm/binding.py)）把网关包装成 `chat_with_tools_stream` facade，`ReActAgent` 只依赖这个 Protocol（[agent.py:45](server/src/forge/agents/react/agent.py:45) `ToolCallingLLM`）。

---

## 7. 解耦设计（横切关注点）

### 7.1 背景任务解耦（agent 跑动 vs SSE 订阅）

最重要的一处解耦：**agent 执行完全脱离 SSE 路由 generator 的生命周期**。

- 运行容器：[turn_run.py:50](server/src/forge/chat/turn_run.py:50) `ChatTurnRun`。agent 跑在独立 `asyncio.Task`（[turn_run.py:115](server/src/forge/chat/turn_run.py:115) `attach_task`）。
- 事件流：agent yields → `emit()` → `store.append_event`（fsync 落盘）+ `broadcaster.publish`（内存广播）（[turn_run.py:213](server/src/forge/chat/turn_run.py:213)）。
- 订阅：[turn_run.py:225](server/src/forge/chat/turn_run.py:225) `subscribe()` 先从 `events.jsonl` 回放（seq > last_seq），再接 broadcaster 实时事件。

效果：**客户端断开 / 浏览器关闭 / SSE 链路死掉都不影响背景 task 跑完并落库**。断线重连只需带 `last_seq` 游标即可不丢不重。

### 7.2 事件溯源（events.jsonl + Broadcaster）

- 落盘：[event_store.py](server/src/forge/chat/event_store.py) `ChatEventStore`（events.jsonl + state.json，每事件带 seq + ts，fsync）
- 广播：[broadcaster.py](server/src/forge/chat/broadcaster.py) `Broadcaster`（内存 fan-out，turn 结束 close 唤醒所有订阅者）
- resume 去重：`baseline_seq` 兜底，避免旧 events 被重放造成内容翻倍（[turn_run.py:245](server/src/forge/chat/turn_run.py:245)）

### 7.3 持久化双轨（chat_db vs run_store）

由 profile 的 `persistence` 字段决定，编排层据此装配不同持久化路径：

- chat：`TurnFinalizer` 写 `chat_messages` 表（[finalizer.py](server/src/forge/chat/finalizer.py)）
- CLI：`RunStorePersistenceLifecycle` 写 JSONL（[run_store.py](server/src/forge/infrastructure/run_store.py)）

二者通过同一套 lifecycle hook 触发，但落点完全不同——**持久化策略与 agent 内核解耦**。

### 7.4 Chat turn 注册表与生命周期管理

[supervisor.py:36](server/src/forge/chat/supervisor.py:36) `ChatTurnSupervisor`（进程内单例）：

- 注册 / 查找 / abort（按 message_id）
- 内存 evict：终态 turn 保留 10 分钟应付重连（[supervisor.py:120](server/src/forge/chat/supervisor.py:120)）
- 磁盘清理：events.jsonl 目录保留 7 天（[supervisor.py:135](server/src/forge/chat/supervisor.py:135)）
- 进程关停：取消所有未完成 turn，各自 finalizer 落库（[supervisor.py:184](server/src/forge/chat/supervisor.py:184)）

---

## 8. 组件分层全景

```
┌─────────────────────────────────────────────────────────────────┐
│ API 层      api/routes/v1/*  (chat / runs / decisions / ...)      │
│             api/server.py + api/lifespan.py (启动装配)             │
├─────────────────────────────────────────────────────────────────┤
│ 编排层      TurnOrchestrator (chat)  │  RunOrchestrator (CLI)      │
│             ChatTurnRun / Supervisor │  RunSupervisor              │
├─────────────────────────────────────────────────────────────────┤
│ Runner 层   ReActRunner.from_profile (装配 lifecycle 组合)         │
├─────────────────────────────────────────────────────────────────┤
│ 扩展点层    AgentLifecycle (Protocol) + MultiLifecycle (组合器)    │
│   ├─ GuardLifecycleAdapter (LoopGuard 收编)                        │
│   ├─ PlanModeLifecycle      (动态工具集 + HITL)                    │
│   ├─ WorkflowLifecycle      (phase 流水线 + gate HITL)             │
│   └─ RunStorePersistenceLifecycle (持久化投影)                    │
├─────────────────────────────────────────────────────────────────┤
│ 内核层      ReActAgent.stream (纯 ReAct, 无 mode 分支)            │
├─────────────────────────────────────────────────────────────────┤
│ 能力层      ToolRegistry / ToolExecutor  │  AGENT_ROLES           │
│             LLMGateway (Pre/Dispatch/Post)│  DecisionRegistry      │
├─────────────────────────────────────────────────────────────────┤
│ 基础设施    DB / Redis / RunStore(JSONL) / EventBus / 向量库      │
└─────────────────────────────────────────────────────────────────┘
```

启动装配顺序（依赖图，见 [lifespan.py:42](server/src/forge/api/lifespan.py:42)）：

```
Logging/Tracing → PromptRegistry → ToolRegistry+AGENT_ROLES
  → load_profiles_at_startup (依赖前三者就绪, 7 项校验)
  → Database → TaskQueue → EventBus+memory hooks → Redis+ModelConfigCache
  → LLMGateway → RAG 组件 → DecisionRegistry.cleanup_loop → ChatTurnSupervisor.cleanup
```

---

## 9. 设计原则提炼（可复用的判断）

1. **扩展点收敛到单一协议**：所有可变行为走 `AgentLifecycle`，内核保持纯净。新增能力 = 写一个 lifecycle，不动 `ReActAgent`。
2. **配置即治理**：mode 由 YAML profile 定义 + 启动期强校验，错误在启动时暴露而非运行时。
3. **物理隔离优于自律**：Plan Mode 让 LLM 看不到被锁工具，而非靠 prompt 约束。
4. **执行与传输解耦**：agent 跑在背景 task，SSE 只是订阅者，断线不影响落库。
5. **失败软降级**：lifecycle / 持久化 / 守护的异常都被隔离，主流程优先跑完。
6. **新旧桥接用适配器**：LoopGuard 不重写，用 `GuardLifecycleAdapter` 接入新协议。

---

## 10. 关键文件速查表

| 关注点 | 文件 |
|--------|------|
| ReAct 内核 | [agent.py](server/src/forge/agents/react/agent.py) |
| 扩展点协议 + 组合器 | [lifecycle.py](server/src/forge/agents/lifecycle.py) |
| Chat 编排 | [orchestrator.py](server/src/forge/chat/orchestrator.py) |
| Chat 运行容器 | [turn_run.py](server/src/forge/chat/turn_run.py) |
| CLI 编排 | [run_orchestrator.py](server/src/forge/agents/run_orchestrator.py) |
| Runner（profile 装配） | [runner.py](server/src/forge/chat/runner.py) |
| Plan Mode | [plan_mode.py](server/src/forge/agents/plan_mode.py) |
| Workflow | [workflow_lifecycle.py](server/src/forge/agents/workflow_lifecycle.py) |
| 持久化投影 | [persistence_lifecycle.py](server/src/forge/agents/persistence_lifecycle.py) |
| HITL | [hitl.py](server/src/forge/agents/hitl.py) |
| Guard 适配 | [lifecycle_adapter.py](server/src/forge/chat/guards/lifecycle_adapter.py) |
| profile 加载校验 | [profiles.py](server/src/forge/agents/profiles.py) |
| profile 配置模型 | [agent_profiles.py](server/src/forge/config/domains/agent_profiles.py) |
| 工具基类 / 注册 | [base.py](server/src/forge/tools/base.py) / [registry.py](server/src/forge/tools/registry.py) |
| 角色 | [factory.py](server/src/forge/agents/roles/factory.py) |
| LLM 网关 | [gateway.py](server/src/forge/llm/gateway.py) |
| 启动装配 | [lifespan.py](server/src/forge/api/lifespan.py) |
| 路由聚合 | [router.py](server/src/forge/api/routes/router.py) |
</content>
</invoke>

