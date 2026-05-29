# Forge 后端代码阅读与学习路线（由浅入深）

> 本文是一条**渐进式代码阅读路线**，配合 [architecture.md](architecture.md) 一起看。
> 每一阶段都给出：**要回答的问题 → 按序阅读的文件（带 `file:line` 跳转） → 自检标准**。
> 重要前提：旧 `adaptive/` 模块已删除，本文以当前真实代码为准。
> 阅读建议：所有 `file:line` 链接可点击直达源码，遇到不懂的类型就跳进去看定义再回来。

---

## 学习路线总览

```
阶段 0  鸟瞰         理解两条执行路径与项目分层          (30 min)
阶段 1  内核         读懂 ReActAgent 一个 step 怎么跑    (深入)
阶段 2  扩展点       AgentLifecycle —— 全架构的钥匙      (最重要)
阶段 3  Chat 路径    一次 Web 对话的完整生命周期         (主链)
阶段 4  能力层       工具系统 + LLM 网关                 (横向)
阶段 5  CLI 路径     plan_exec / workflow + HITL         (进阶)
阶段 6  治理与解耦   agent_profiles / 事件溯源 / 持久化  (融会贯通)
```

学习心法：**先看「内核如何无差别地跑」，再看「差异如何被外置」**。不要一开始就钻 mode 分支——因为内核里根本没有 mode 分支。

---

## 阶段 0：鸟瞰（先建立坐标系）

**要回答的问题**：后端有几条执行路径？它们共享什么、不同什么？

按序阅读：

1. [architecture.md](architecture.md) 第 0–1 节 —— 先建立「两条路径共享 ReActAgent 内核」的总图。
2. [server/src/forge/api/routes/router.py](server/src/forge/api/routes/router.py) —— v1 路由聚合，看后端到底挂了哪些功能域（chat / runs / decisions / artifacts / sessions / ...）。
3. [server/src/forge/api/lifespan.py:42](server/src/forge/api/lifespan.py:42) `lifespan()` —— 启动装配顺序，这是组件依赖图的「事实来源」。

**自检**：能用一句话说出 chat 路径与 runs 路径的入口、编排类、持久化落点各是什么（见 architecture.md 第 1 节表格）。

---

## 阶段 1：内核 —— ReActAgent 一个 step 怎么跑

> 这是全后端最该先读透的一个函数。读懂它，后面所有扩展点才有「挂载坐标系」。

**要回答的问题**：给定 user input + 工具集，agent 如何循环「调 LLM → 执行工具 → 再调 LLM」直到产出答案？

核心文件：[server/src/forge/agents/react/agent.py](server/src/forge/agents/react/agent.py)

按序精读：

1. [agent.py:92](server/src/forge/agents/react/agent.py:92) `ReActAgent.__init__` —— 构造：工具集来源（自定义 vs ToolRegistry 全集）、默认 schema 缓存、executor 注入。
2. [agent.py:45](server/src/forge/agents/react/agent.py:45) `ToolCallingLLM` Protocol —— agent 只依赖这个抽象，**不直接依赖 LLM 网关**（解耦点）。
3. [agent.py:188](server/src/forge/agents/react/agent.py:188) `stream()` —— 主流式循环，**逐行读完这个方法**。重点抓这几个坐标：
   - [agent.py:248](server/src/forge/agents/react/agent.py:248) `for _step in range(self._max_steps)` —— 主循环骨架
   - [agent.py:264](server/src/forge/agents/react/agent.py:264) `resolve_tools` —— 本步用哪份工具 schema（动态工具集入口）
   - [agent.py:301](server/src/forge/agents/react/agent.py:301) 流式 LLM 调用 —— content delta / reasoning delta / tool_calls 如何边生成边 yield
   - [agent.py:434](server/src/forge/agents/react/agent.py:434) 无 tool_calls ⇒ 终态 break
   - [agent.py:468](server/src/forge/agents/react/agent.py:468) 工具执行循环 —— **并行批处理**的精妙之处
4. [agent.py:624](server/src/forge/agents/react/agent.py:624) `_execute_with_lifecycle` —— 单个工具的完整执行链：before_tool_call 拦截 → 真实执行 → on_tool_result 替换。

**容易卡住的点**：

- 工具并行：[agent.py:494](server/src/forge/agents/react/agent.py:494) `is_parallelism_safe` 判断 → 连续 safe 工具收成 batch 用 `asyncio.gather`，unsafe 串行。结合 [base.py:49](server/src/forge/tools/base.py:49) 的 `parallelism_safe` 类属性一起看。
- abort：搜索 `abort_event` 在本文件出现的所有位置（step 头 / chunk 内 / 工具派发前），理解「优雅中断」如何不打断已 in-flight 的工具。

**自检**：合上代码，能在纸上画出一个 step 内 hook 的调用顺序（resolve_tools → before_step → LLM → before_tool_call → on_tool_result → after_step）。

---

## 阶段 2：扩展点 —— AgentLifecycle（全架构的钥匙）

> 这是 Forge 架构的「主扩展点」。阶段 1 里那些 `lifecycle.xxx(...)` 调用，挂的就是这里的实现。

**要回答的问题**：mode 差异（chat / plan_exec / workflow）凭什么不写进 ReActAgent？

核心文件：[server/src/forge/agents/lifecycle.py](server/src/forge/agents/lifecycle.py)

按序精读：

1. 文件头 docstring（[lifecycle.py:1](server/src/forge/agents/lifecycle.py:1)）—— 设计意图：所有扩展点统一暴露、默认 no-op、异常隔离。
2. [lifecycle.py:44](server/src/forge/agents/lifecycle.py:44) 起的 6 个 dataclass —— `RunContext` / `StepContext` / `StepDecision` / `StepOutcome` / `ToolCallVeto` / `RunResult`。这些是 hook 的输入输出契约。
3. [lifecycle.py:120](server/src/forge/agents/lifecycle.py:120) `AgentLifecycle` Protocol —— 8 个 hook 的语义（尤其注意每个返回值「None 代表什么」）。
4. [lifecycle.py:193](server/src/forge/agents/lifecycle.py:193) `MultiLifecycle` —— **本阶段的高潮**。三种合并策略：
   - 「首个非 None 胜出」：[lifecycle.py:223](server/src/forge/agents/lifecycle.py:223) `resolve_tools` / [lifecycle.py:234](server/src/forge/agents/lifecycle.py:234) `before_step` / [lifecycle.py:252](server/src/forge/agents/lifecycle.py:252) `before_tool_call`
   - 「pipeline 累计」：[lifecycle.py:267](server/src/forge/agents/lifecycle.py:267) `on_tool_result`
   - 「全部都调 + 异常隔离」：`on_start` / `after_step` / `on_complete` / `on_error`

**自检**：能解释「为什么 `resolve_tools` 用首个胜出，而 `on_tool_result` 用 pipeline 累计」——前者多个 lifecycle 抢工具集会冲突，后者每个 lifecycle 都可能想改写结果（如先脱敏再落 artifact）。

---

## 阶段 3：Chat 路径 —— 一次 Web 对话的完整生命周期

> 现在把内核 + 扩展点放进真实的 chat 主链里，看一次 `/chat/completions` 从请求到落库发生了什么。

**要回答的问题**：客户端断开后，为什么 agent 还能跑完并落库？SSE 断线重连为什么不丢不重？

按序阅读（沿调用链）：

1. [server/src/forge/api/routes/v1/chat.py:78](server/src/forge/api/routes/v1/chat.py:78) `chat_completions` —— 路由入口。注意：**同步 start_turn → 立即返回 SSE 订阅流**，路由不阻塞 agent。
2. [server/src/forge/chat/orchestrator.py:79](server/src/forge/chat/orchestrator.py:79) `TurnOrchestrator.start_turn` —— prepare（DB 占位）→ 建 `ChatTurnRun` → `attach_task` 启背景执行。
3. [orchestrator.py:190](server/src/forge/chat/orchestrator.py:190) `_execute_new_turn` —— 背景执行体：lifecycle 事件 → 上下文组装 + 压缩 → 跑 runner → finalize 落库。
4. [server/src/forge/chat/runner.py:100](server/src/forge/chat/runner.py:100) `ReActRunner.run` —— 装配 lifecycle（这里只有 GuardLifecycleAdapter）→ 构造 ReActAgent → 跑 stream 透传事件。
5. [orchestrator.py:337](server/src/forge/chat/orchestrator.py:337) `_setup_runner` + [runner.py:181](server/src/forge/chat/runner.py:181) `from_profile` —— **mode 路由的唯一入口**：据 `get_agent_profile(mode)` 过滤工具、定 max_steps。

背景任务解耦机制（本阶段重点）：

6. [server/src/forge/chat/turn_run.py:50](server/src/forge/chat/turn_run.py:50) `ChatTurnRun` —— 运行容器。
   - [turn_run.py:115](server/src/forge/chat/turn_run.py:115) `attach_task` —— agent 跑在独立 asyncio.Task
   - [turn_run.py:213](server/src/forge/chat/turn_run.py:213) `emit` —— 落盘（fsync）+ 广播
   - [turn_run.py:225](server/src/forge/chat/turn_run.py:225) `subscribe` —— 回放（events.jsonl）+ 实时（broadcaster）合流，`max(last_seq, baseline_seq)` 去重
7. [server/src/forge/chat/supervisor.py:36](server/src/forge/chat/supervisor.py:36) `ChatTurnSupervisor` —— 进程内注册表 + evict / 磁盘清理 / 关停。

辅助阅读（按需）：

- [server/src/forge/chat/preparer.py](server/src/forge/chat/preparer.py) `TurnPreparer` —— session / 占位 message 准备
- [server/src/forge/chat/finalizer.py](server/src/forge/chat/finalizer.py) `TurnFinalizer` —— 终态落 `chat_messages` DB
- [server/src/forge/chat/resumer.py](server/src/forge/chat/resumer.py) + [orchestrator.py:256](server/src/forge/chat/orchestrator.py:256) `_execute_resume` —— **续写**路径（含流式去重 `ResumeStreamDedup`）

**自检**：能讲清「`/chat/stop` 怎么停一个 turn」——[chat.py:194](server/src/forge/api/routes/v1/chat.py:194) `chat_stop` → `run.abort()` → `abort_event.set()` → agent 检测到后 break → finalizer 落 aborted。整条链路不依赖 SSE 是否还活着。

---

## 阶段 4：能力层 —— 工具系统 + LLM 网关

> 内核依赖两个外部能力：工具（做事）和 LLM（思考）。本阶段读懂它们如何被抽象。

### 4.1 工具系统

1. [server/src/forge/tools/base.py:33](server/src/forge/tools/base.py:33) `Tool(ABC)` —— `run`/`arun` 二选一 + 元数据（parallelism_safe / dangerous / required_scope / allowed_roles）。
2. [server/src/forge/tools/registry.py:29](server/src/forge/tools/registry.py:29) `@register_tool` —— 装饰器注册 + 注册期 schema 预计算缓存（[registry.py:41](server/src/forge/tools/registry.py:41)）。
3. `server/src/forge/tools/executor.py` `ToolExecutor` —— guardrail 流水线（access → permission → rate_limit → dangerous_op）+ workspace 路径策略 + `aexecute`（IO 工具走 arun，CPU 工具扔 to_thread）。

### 4.2 LLM 网关

1. [server/src/forge/llm/gateway.py:1](server/src/forge/llm/gateway.py:1) 文件头 —— 一眼看清调用链：Pre → Dispatcher → Provider → Post。
2. [gateway.py:54](server/src/forge/llm/gateway.py:54) `LLMGateway` —— 业务层唯一入口。
3. `server/src/forge/llm/binding.py` `GatewayLLMAdapter` —— 把网关包装成 `ToolCallingLLM` facade（阶段 1 见过的 Protocol）。**这就是内核与网关的解耦缝合点**。

**自检**：能解释「ReActAgent 为什么不知道自己在用哪个 provider」——它只持有 `ToolCallingLLM`，provider / 路由 / 熔断 / fallback 全在网关里，对它透明。

---

## 阶段 5：CLI 路径 —— plan_exec / workflow + HITL

> 现在回到阶段 2 的承诺：mode 差异如何用 lifecycle 组合实现。CLI 路径是扩展点的「集大成者」。

**要回答的问题**：Plan Mode 怎么让 LLM「看不到」写工具？用户怎么在中途批准/否决，agent 怎么醒来？

按序阅读：

1. [server/src/forge/agents/run_orchestrator.py:47](server/src/forge/agents/run_orchestrator.py:47) `RunOrchestrator` —— CLI 编排（对标 chat 的 TurnOrchestrator）。
2. [run_orchestrator.py:196](server/src/forge/agents/run_orchestrator.py:196) `_build_lifecycles` —— **本阶段核心**：据 profile flag 装配 `[Guards, (PlanMode), (Workflow), (Persistence)]`。看清「装配逻辑 = 全部的 mode 路由」。
3. [server/src/forge/agents/plan_mode.py:52](server/src/forge/agents/plan_mode.py:52) `PlanModeLifecycle`：
   - [plan_mode.py:41](server/src/forge/agents/plan_mode.py:41) `PLAN_MODE` ContextVar —— 阶段状态
   - [plan_mode.py:94](server/src/forge/agents/plan_mode.py:94) `resolve_tools` —— 据状态返回 readonly / full schema（动态工具集落地）
   - [plan_mode.py:97](server/src/forge/agents/plan_mode.py:97) `before_tool_call` —— 拦截 exit_plan_mode → HITL → 解锁
4. [server/src/forge/agents/hitl.py:67](server/src/forge/agents/hitl.py:67) `DecisionRegistry` —— 人机交互内核：`create` 注册 PendingDecision → 主 agent `await event.wait()`；外部 `resolve` 唤醒（[hitl.py:103](server/src/forge/agents/hitl.py:103)）；TTL 守护 `cleanup_loop`（[hitl.py:127](server/src/forge/agents/hitl.py:127)）。
5. [server/src/forge/api/routes/v1/decisions.py](server/src/forge/api/routes/v1/decisions.py) —— 外部提交决策的路由（`POST /v1/decisions/{token}`）。
6. [server/src/forge/agents/workflow_lifecycle.py:86](server/src/forge/agents/workflow_lifecycle.py:86) `WorkflowLifecycle` —— 同款 HITL，拦截 `advance_phase`，命中 gate 走 `workflow_gate` 决策（[workflow_lifecycle.py:154](server/src/forge/agents/workflow_lifecycle.py:154)）。
7. [server/src/forge/agents/persistence_lifecycle.py:108](server/src/forge/agents/persistence_lifecycle.py:108) `on_tool_result` —— 大产物落 artifact 回灌占位（节省 LLM 上下文）。

子 agent 角色（按需）：[server/src/forge/agents/roles/factory.py:20](server/src/forge/agents/roles/factory.py:20) `_builtin_roles` —— 7 个内置角色的工具/模型矩阵。

**自检**：能对照阶段 1 的 step 序列，指出 PlanModeLifecycle 的每个 hook 分别挂在 step 的哪个坐标，以及 `await event.wait()` 阻塞时整个 background task 处于什么状态（挂起但不占 CPU，events.jsonl 已写 blocked 状态）。

---

## 阶段 6：治理与解耦（融会贯通）

> 把前面零散的「为什么这么设计」收口。

1. **配置即治理**：
   - [server/src/forge/config/domains/agent_profiles.py:38](server/src/forge/config/domains/agent_profiles.py:38) `AgentProfile` 模型
   - [server/src/forge/agents/profiles.py:26](server/src/forge/agents/profiles.py:26) `load_profiles_at_startup` —— **逐条读 7 项启动校验**，理解「错误在启动暴露而非运行时」
   - [server/config/sys_config.dev.yaml:137](server/config/sys_config.dev.yaml:137) `agent_profiles` 段 —— 对照三个 profile 的字段差异（tools_allowed / readonly_tools / plan_mode_initial / persistence / requires_template）
2. **守护体系收编**：[server/src/forge/chat/guards/lifecycle_adapter.py:26](server/src/forge/chat/guards/lifecycle_adapter.py:26) `GuardLifecycleAdapter` —— 旧 LoopGuard 不重写，用适配器接入新协议（新旧桥接范例）。
3. **事件溯源**：[server/src/forge/chat/event_store.py](server/src/forge/chat/event_store.py) + [server/src/forge/chat/broadcaster.py](server/src/forge/chat/broadcaster.py) —— 落盘 + 广播双写如何支撑断线重连。
4. **持久化双轨**：对比 [finalizer.py](server/src/forge/chat/finalizer.py)（chat → DB）与 [run_store.py](server/src/forge/infrastructure/run_store.py)（CLI → JSONL），理解同一套 lifecycle hook 落到不同存储。

**自检（终极）**：尝试口述「如果要新增一个 mode（比如 `review_only`），需要改哪些地方」。正确答案应是：① YAML 加一段 profile；② 写一个 prompt 模板；③ （若需特殊行为）写一个新 lifecycle 并在编排层的 `_build_lifecycles` 装配。**完全不需要动 ReActAgent**。能答到这，说明已掌握整个架构。

---

## 附录 A：按「我想改 X」反查入口

| 我想… | 从哪开始 |
|-------|---------|
| 加一个新工具 | [base.py:33](server/src/forge/tools/base.py:33) + `@register_tool`，再加进某 profile 的 `tools_allowed` |
| 加一个新 mode | [sys_config.dev.yaml:137](server/config/sys_config.dev.yaml:137) 加 profile + prompt 模板 |
| 改 agent 单步行为 | 写新 `AgentLifecycle`（[lifecycle.py:120](server/src/forge/agents/lifecycle.py:120)），别改内核 |
| 改 SSE 事件协议 | [chat.py:54](server/src/forge/api/routes/v1/chat.py:54) `_sse` + 前端 `web/src/types/index.ts` |
| 改 LLM 路由 / 加 provider | [gateway.py](server/src/forge/llm/gateway.py) + `llm/providers/` + `llm/registry.py` |
| 加一个守护规则 | `guards/` 实现 LoopGuard，注册进 [runner.py:50](server/src/forge/chat/runner.py:50) `_default_guard_factories` |
| 改人机交互 | [hitl.py:67](server/src/forge/agents/hitl.py:67) `DecisionRegistry` + [decisions.py](server/src/forge/api/routes/v1/decisions.py) |

## 附录 B：阅读顺序一图流

```
阶段0 router.py / lifespan.py
   │
阶段1 agent.py::stream  ◀── 全后端最该先读透的函数
   │
阶段2 lifecycle.py::MultiLifecycle  ◀── 架构的钥匙
   │
   ├── 阶段3 orchestrator.py → turn_run.py → supervisor.py   (Chat 主链)
   │
   ├── 阶段4 tools/base.py + llm/gateway.py + binding.py      (能力层)
   │
   └── 阶段5 run_orchestrator.py → plan_mode.py → hitl.py     (CLI 进阶)
              │
阶段6 profiles.py + agent_profiles.yaml + guards/adapter      (融会贯通)
```
</content>
</invoke>
