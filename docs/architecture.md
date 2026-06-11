# Forge 后端架构设计

> 本文基于 `server/src/forge` 当前**真实代码**编写（非历史方案）。
> 重要：旧版 `adaptive/`（DAG / Validator / Wave 调度）与旧 **CLI 执行路径**（`/v1/runs` + HITL + RunStore + Plan/Workflow lifecycle）均已删除。历史文档与本文不一致时，以本文为准。
> 所有 `file:line` 引用均可点击跳转到源码。

---

## 0. 一句话总览

Forge 后端是一个**以 ReAct Agent 为内核、以可组合 AgentLifecycle 为唯一扩展机制**的智能体执行框架。

它的核心设计判断只有一句话：

> **所有模式（mode）差异都被外置成 `AgentLifecycle` 的不同组合，绝不在 `ReActAgent` 内部用 `if mode == ...` 分支。**

理解了这句话，就理解了整个后端的解耦骨架。

---

## 1. 服务端职责边界（最顶层的分工）

服务端有两个对外能力面，共享同一个 `LLMGateway`：

| 维度 | Chat 路径（Web 端，唯一智能体执行路径） | 对外 LLM 网关端点（CLI / 第三方直连） |
|------|------------------------------------|--------------------------------------|
| 入口路由 | `POST /api/v1/chat/*` | `POST /api/v1/llm/chat/completions` |
| 鉴权 | JWT（`AuthenticatedUser`） | API Key（`ApiKeyUser`，X-API-Key） |
| 服务端职责 | 完整智能体执行（上下文/记忆/工具/守护） | 仅网关化 LLM 调用（治理 + 计费 + 路由） |
| 持久化 | `chat_messages` 数据库 + `events.jsonl` | 无（客户端自行管理会话） |
| 响应形态 | Forge 自有 SSE 事件协议 | OpenAI 兼容 JSON / SSE chunk |

- Chat 编排：[orchestrator.py](server/src/forge/chat/orchestrator.py) `TurnOrchestrator`
- 对外 LLM 端点：[llm.py](server/src/forge/api/routes/v1/llm.py)
- 共享内核：[agent.py](server/src/forge/agents/react/agent.py) `ReActAgent`

```
   Web 端  ── /chat ──▶ TurnOrchestrator ──▶ ReActAgent.stream(lifecycle=[Guards])
                                                   │
                                                   ▼
   CLI 端  ── /llm/chat/completions ─────────▶ LLMGateway（Pre → Dispatch → Post）
   （未来胖客户端: agent loop 在本机跑,            ▲
     工具操作本地文件, HITL 走终端交互）            └── chat 路径也经 GatewayLLMAdapter 走这里
```

**未来 CLI 客户端的约定**：胖客户端形态——复用 `forge.agents` 内核（依赖干净，零 FastAPI/DB 依赖），在客户端本地装配 Plan/Workflow 类 lifecycle 与本地 HITL，LLM 调用直连 `/v1/llm/chat/completions` 享受服务端模型治理与成本治理。

---

## 2. 核心内核：ReActAgent

`ReActAgent` 是经典 ReAct（Reason + Act）循环，但用 **LLM 原生 function calling**，不做 "Thought/Action/Observation" 文本解析（工业实现更稳定）。

主流式入口：[agent.py:188](server/src/forge/agents/react/agent.py:188) `ReActAgent.stream(...)`

单步（step）执行序列（这就是所有扩展点的「挂载坐标系」）：

```
on_start                         一次性
└─ for step in range(max_steps):
     resolve_tools               每步       ← 动态工具集
     before_step                 每步       ← 注入引导 / 强制纯文本
     [LLM 流式调用]
     if 无 tool_calls: break(终态)
     for tc in tool_calls:
        before_tool_call         每工具     ← veto 拦截
        [执行 或 用 veto 替代]
        on_tool_result           每工具     ← 结果替换管道
     after_step                  每步
on_complete / on_error           终态
```

关键设计点：

- **`lifecycle=None` ⇒ 纯 ReAct**。所有 hook 调用前都判空，没有 lifecycle 时行为完全等价于原始 ReAct。
- **工具并行调度**：同一 step 内连续的 `parallelism_safe=True` 工具会被收集成 batch 用 `asyncio.gather` 并行，unsafe 工具严格串行，整体保留 LLM 给的顺序。见 [base.py:49](server/src/forge/tools/base.py:49) 的 `parallelism_safe`。
- **abort 优雅中断**：每步、每个 chunk、每个工具派发前都检查 `abort_event`，已在 flight 的工具不强行打断（避免副作用），未派发的补 `aborted` 的 tool_result。
- **可观测性内建**：每层都有 `span(...)` 埋点（stream / step / llm_call / tool 四级）。

---

## 3. 第一扩展点（最核心）：AgentLifecycle

> 这是整个架构的「主扩展点」。其他扩展点都是围绕它服务的。

定义文件：[lifecycle.py](server/src/forge/agents/lifecycle.py)

### 3.1 抽象契约（ABC）

`class AgentLifecycle(ABC)`，8 个 hook **全部抽象**。需要按需覆写的实现继承 `NoopLifecycle`，由其提供默认 no-op：

| Hook | 签名 | 返回语义 | 典型用途 |
|------|------|---------|---------|
| `on_start` | `(RunContext)` | — | 初始化 |
| `resolve_tools` | `(StepContext) -> list[dict] \| None` | None=用默认；list=替换本步 schema | **动态工具集** |
| `before_step` | `(StepContext) -> StepDecision \| None` | 注入 system / 强制纯文本 | LoopGuard 引导 / 收尾 |
| `before_tool_call` | `(ToolCall, StepContext) -> ToolCallVeto \| None` | None=放行；veto=替代执行 | 工具拦截 |
| `on_tool_result` | `(ToolCall, Message) -> Message \| None` | None=不改；Message=替换 | 结果改写管道 |
| `after_step` | `(StepContext, StepOutcome)` | — | checkpoint |
| `on_complete` | `(RunResult)` | — | 终态收尾 |
| `on_error` | `(BaseException, RunResult)` | — | 终态收尾 |

共享数据类型（全部 frozen dataclass，不可变快照）：`RunContext` / `StepContext` / `StepDecision` / `StepOutcome` / `ToolCallVeto` / `RunResult`。

### 3.2 组合器：MultiLifecycle（解耦的关键）

`class MultiLifecycle`。把多个 lifecycle 串成一个，按 hook 语义采用三种合并策略：

| 合并策略 | 适用 hook | 语义 |
|---------|----------|------|
| **首个非 None 胜出** | `resolve_tools` / `before_step` / `before_tool_call` | 找到第一个返回非 None 就返回，避免互相覆盖工具集 / 拦截语义混乱 |
| **pipeline 累计** | `on_tool_result` | 依次调用，后者基于前者结果 |
| **全部都调** | `on_start` / `after_step` / `on_complete` / `on_error` | 各自副作用，逐个执行 |

**单 lifecycle 异常被隔离**：每个 hook 调用都包 try/except 打日志，一个 lifecycle 挂掉不影响其他 lifecycle 和主流程。

### 3.3 这套机制如何实现「mode 差异外置」

mode = lifecycle 组合，由编排层装配（**这是全部的 mode 路由逻辑**）：

- Chat：`[GuardLifecycleAdapter]`（仅兜底守护）— [runner.py](server/src/forge/chat/runner.py)

当前服务端仅 chat 一个 mode。机制本身是通用的：未来新增服务端 mode、或 CLI 客户端本地装配 Plan/Workflow 类 lifecycle，都按同样方式扩展——`ReActAgent` 对此**完全无感知**，它只看到一个 `AgentLifecycle`。

---

## 4. 围绕 AgentLifecycle 的具体实现组件

### 4.1 LoopGuard → GuardLifecycleAdapter（守护体系的收编）

旧的 LoopGuard 体系（死循环 / 步数 / token / 墙钟守护）通过一个适配器无侵入接入 lifecycle 协议：

- 协议与守护实现：[guards/base.py](server/src/forge/chat/guards/base.py)，四个内置守护 `StepSafetyNet` / `StuckDetector` / `TokenBudgetGuard` / `WallClockGuard`
- 适配器：[lifecycle_adapter.py](server/src/forge/chat/guards/lifecycle_adapter.py) `GuardLifecycleAdapter`，只做 `StepContext → LoopState` 字段映射 + 合并多个 guidance 到 `before_step`

设计价值：守护逻辑无需重写就接入了新协议，**新旧体系桥接**的范例。

---

## 5. 配置即治理：agent_profiles 扩展点

新增一个 mode **无需写 Python 代码**，只需在 YAML 加一段 profile + 一个 prompt 模板。

- 配置模型：[agent_profiles.py](server/src/forge/config/domains/agent_profiles.py) `AgentProfile`（pydantic，`extra="forbid"` 严格校验）
- 实际配置：`server/config/sys_config.dev.yaml` 的 `agent_profiles` 段（当前仅 chat profile）
- 加载 + 启动期校验：[profiles.py](server/src/forge/agents/profiles.py) `load_profiles_at_startup`

启动期 5 项强校验（任一不过直接拒绝启动）：

1. `tools_allowed` 每个工具名在 ToolRegistry 已注册
2. `sub_agents_allowed` 每个 role 已注册
3. `sub_agents_allowed` 非空 ⇔ `spawn_subagent in tools_allowed`
4. `system_prompt_template` 在 PromptRegistry 存在
5. `model_profile` 在 `model_profiles` 字典定义

profile 关键字段：`persistence`（chat_db / none）、`model_profile`（fast / smart / strong）、`tools_allowed`、`sub_agents_allowed`、`max_steps`。

Runner 据 profile 自动过滤工具：[runner.py](server/src/forge/chat/runner.py) `ReActRunner.from_profile`。

---

## 6. 其余扩展点

### 6.1 Tool / ToolRegistry（工具扩展点）

- 基类：[base.py:33](server/src/forge/tools/base.py:33) `class Tool(ABC)`。实现 `run`（CPU bound）或 `arun`（IO bound）之一。元数据：`parallelism_safe` / `dangerous` / `required_scope` / `allowed_roles` / `path_role_whitelist`。
- 注册：[registry.py:29](server/src/forge/tools/registry.py:29) `@register_tool` 装饰器，注册期预计算 schema 缓存。
- 执行：`ToolExecutor`（`server/src/forge/tools/executor.py`）带 guardrail 流水线（access → permission → rate_limit → dangerous_op）+ workspace 路径策略。

### 6.2 AgentRole（子 agent 角色扩展点）

[roles/factory.py](server/src/forge/agents/roles/factory.py) `AgentRole`。7 个内置角色（triage / developer / architect / reviewer / qa / ra / devops），每个声明 `allowed_tools` / `model_preference` / `can_write` / `write_path_prefixes`。

- 动态扩展：`register_custom_agent_role`（进程级，默认不覆盖内置）

子 agent 通过 `spawn_subagent` 工具派发，受 profile 的 `sub_agents_allowed` 白名单约束，spawn 层 `SUBAGENT_DENY_TOOLS` 再硬剥离写类/二级派发工具。

### 6.3 LLM 网关（Provider / 中间件 / 路由扩展点）

[gateway.py](server/src/forge/llm/gateway.py) `LLMGateway` 是业务层**唯一对外入口**。调用链：

```
LLMRequest
  → PrePipeline   (validator → rate_limit → budget → dedup → cache)
  → LLMDispatcher (router → chain 遍历 → 熔断 → 重试 → fallback)
  → Provider
  → PostPipeline  (cache_write → dedup_complete → audit)
  → LLMResponse
```

- 中间件可插拔：`from_settings` 接受自定义 Pre/Post 链
- Provider 动态注册：`llm/registry.py` + `llm/providers/`
- 对 agent 的适配：`GatewayLLMAdapter`（[binding.py](server/src/forge/llm/binding.py)）把网关包装成 `chat_with_tools_stream` facade，`ReActAgent` 只依赖 `ToolCallingLLM` ABC（[contracts.py](server/src/forge/llm/contracts.py)）。

### 6.4 对外 LLM 端点（网关的 HTTP 形态）

[llm.py](server/src/forge/api/routes/v1/llm.py) 把 `LLMGateway` 以 OpenAI 兼容 HTTP 端点形式暴露：

- 鉴权：`ApiKeyUser`（X-API-Key，`user_api_keys` 表存 SHA256 哈希，吊销/过期/禁用检查）
- model 三形态：空 → 系统默认链；`fast|smart|strong` → 档位链；`provider:model` → 显式 pin
- 非流式 → chat.completion JSON（扩展 `forge.*` 元信息字段）；流式 → SSE chat.completion.chunk + `[DONE]`，带 tools 时工具调用在最终块一次性下发（网关层已聚合）
- 配额/预算/限流/缓存/审计全部由网关 Pre/Post 中间件按 user_id 生效，端点本身零治理逻辑

---

## 7. 解耦设计（横切关注点）

### 7.1 背景任务解耦（agent 跑动 vs SSE 订阅）

最重要的一处解耦：**agent 执行完全脱离 SSE 路由 generator 的生命周期**。

- 运行容器：[turn_run.py](server/src/forge/chat/turn_run.py) `ChatTurnRun`。agent 跑在独立 `asyncio.Task`（`attach_task`）。
- 事件流：agent yields → `emit()` → `store.append_event`（fsync 落盘）+ `broadcaster.publish`（内存广播）。
- 订阅：`subscribe()` 先从 `events.jsonl` 回放（seq > last_seq），再接 broadcaster 实时事件。

效果：**客户端断开 / 浏览器关闭 / SSE 链路死掉都不影响背景 task 跑完并落库**。断线重连只需带 `last_seq` 游标即可不丢不重。

### 7.2 事件溯源（events.jsonl + Broadcaster）

- 落盘：[event_store.py](server/src/forge/chat/event_store.py) `ChatEventStore`（events.jsonl + state.json，每事件带 seq + ts，fsync）
- 广播：[broadcaster.py](server/src/forge/chat/broadcaster.py) `Broadcaster`（内存 fan-out，turn 结束 close 唤醒所有订阅者）
- resume 去重：`baseline_seq` 兜底，避免旧 events 被重放造成内容翻倍

### 7.3 Chat turn 注册表与生命周期管理

[supervisor.py](server/src/forge/chat/supervisor.py) `ChatTurnSupervisor`（进程内单例）：

- 注册 / 查找 / abort（按 message_id）
- 内存 evict：终态 turn 保留 10 分钟应付重连
- 磁盘清理：events.jsonl 目录保留 7 天
- 进程关停：取消所有未完成 turn，各自 finalizer 落库

---

## 8. 组件分层全景

```
┌─────────────────────────────────────────────────────────────────┐
│ API 层      api/routes/v1/*  (chat / llm / sessions / kb / ...)   │
│             api/server.py + api/lifespan.py (启动装配)             │
├─────────────────────────────────────────────────────────────────┤
│ 编排层      TurnOrchestrator / ChatTurnRun / ChatTurnSupervisor   │
├─────────────────────────────────────────────────────────────────┤
│ Runner 层   ReActRunner.from_profile (装配 lifecycle 组合)         │
├─────────────────────────────────────────────────────────────────┤
│ 扩展点层    AgentLifecycle (ABC) + MultiLifecycle (组合器)         │
│   └─ GuardLifecycleAdapter (LoopGuard 收编)                        │
├─────────────────────────────────────────────────────────────────┤
│ 内核层      ReActAgent.stream (纯 ReAct, 无 mode 分支)            │
├─────────────────────────────────────────────────────────────────┤
│ 能力层      ToolRegistry / ToolExecutor  │  AGENT_ROLES           │
│             LLMGateway (Pre/Dispatch/Post + 对外 HTTP 端点)        │
├─────────────────────────────────────────────────────────────────┤
│ 基础设施    DB / Redis / JSONL / EventBus / 向量库                │
└─────────────────────────────────────────────────────────────────┘
```

启动装配顺序（依赖图，见 [lifespan.py](server/src/forge/api/lifespan.py)）：

```
Logging/Tracing → PromptRegistry → ToolRegistry+AGENT_ROLES
  → load_profiles_at_startup (依赖前三者就绪, 5 项校验)
  → Database → TaskQueue → EventBus+memory/digest/recall hooks
  → Redis+ModelConfigCache → LLMGateway → RAG 组件 → ChatTurnSupervisor.cleanup
```

---

## 9. 设计原则提炼（可复用的判断）

1. **扩展点收敛到单一协议**：所有可变行为走 `AgentLifecycle`，内核保持纯净。新增能力 = 写一个 lifecycle，不动 `ReActAgent`。
2. **配置即治理**：mode 由 YAML profile 定义 + 启动期强校验，错误在启动时暴露而非运行时。
3. **执行与传输解耦**：agent 跑在背景 task，SSE 只是订阅者，断线不影响落库。
4. **失败软降级**：lifecycle / 持久化 / 守护的异常都被隔离，主流程优先跑完。
5. **新旧桥接用适配器**：LoopGuard 不重写，用 `GuardLifecycleAdapter` 接入新协议。
6. **服务端瘦身**：智能体执行只为 Web Chat 服务；CLI 形态走胖客户端 + 网关直连，服务端不为远程执行背负 HITL/RunStore/沙箱复杂度。

---

## 10. 关键文件速查表

| 关注点 | 文件 |
|--------|------|
| ReAct 内核 | [agent.py](server/src/forge/agents/react/agent.py) |
| 扩展点协议 + 组合器 | [lifecycle.py](server/src/forge/agents/lifecycle.py) |
| Chat 编排 | [orchestrator.py](server/src/forge/chat/orchestrator.py) |
| Chat 运行容器 | [turn_run.py](server/src/forge/chat/turn_run.py) |
| Runner（profile 装配） | [runner.py](server/src/forge/chat/runner.py) |
| 对外 LLM 端点 | [llm.py](server/src/forge/api/routes/v1/llm.py) |
| Guard 适配 | [lifecycle_adapter.py](server/src/forge/chat/guards/lifecycle_adapter.py) |
| profile 加载校验 | [profiles.py](server/src/forge/agents/profiles.py) |
| profile 配置模型 | [agent_profiles.py](server/src/forge/config/domains/agent_profiles.py) |
| 工具基类 / 注册 | [base.py](server/src/forge/tools/base.py) / [registry.py](server/src/forge/tools/registry.py) |
| 角色 | [factory.py](server/src/forge/agents/roles/factory.py) |
| LLM 网关 | [gateway.py](server/src/forge/llm/gateway.py) |
| 启动装配 | [lifespan.py](server/src/forge/api/lifespan.py) |
| 路由聚合 | [router.py](server/src/forge/api/routes/router.py) |
