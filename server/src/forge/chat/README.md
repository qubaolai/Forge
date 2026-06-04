# `agent_platform.chat` — 对话 turn 业务编排

把一次 chat turn (用户发一条消息 / 点继续生成 → 流式返回结果) 的所有业务流程从路由层剥离, 5 个职责单一的角色协作完成. 路由层 (`api/routes/v1/chat.py`) 只剩约 100 行 HTTP / SSE 边界.

## 为什么需要这层

重构前: `_stream_chat` 巨函数 ~350 行, 在一个函数里完成 11 件事:
session 校验 / agent 加载 / 消息持久化 / SSE 生命周期事件 / system prompt 渲染 / context 构建 / LLM 调用 / ReAct 循环 / 事件转换 / 异常处理 / 状态写回 / 事件发布.

随之而来的问题:
- 加新功能 (eg 主动上下文压缩 / LoopGuard / 用户继续生成) 都要在这个函数里改, 改动面巨大
- 业务流程跟 chat 路由耦合: 加新的 agent mode (plan-execute / supervisor) 要改路由
- 单测难写: 任一职责变更都要重新 mock 整套依赖

拆完后:
- 每个角色单元测试覆盖自己的职责
- chat 路由稳定, 业务演进只动 `chat/` 内部
- 通过 Runner 注册表可平滑加新 agent mode

## 模块速览

```
chat/
├── types.py            ← TurnContext (frozen 上下文) / RunResult (累计结果) / ResumeState (恢复快照)
├── preparer.py         ← TurnPreparer: DB 校验 + user_msg 持久化 + assistant 占位
├── runner.py           ← ReActRunner.from_profile: 按 agent_profile 装配 agent + lifecycle
├── tools.py            ← resolve_chat_tools: 按 chat profile.tools_allowed 过滤工具
├── kb_resolver.py      ← 解析用户可见知识库列表 (供 system prompt)
├── model_meta.py       ← 模型上下文窗口解析
├── guards/             ← LoopGuard 框架: StepSafetyNet / StuckDetector / TokenBudget / WallClock
├── finalizer.py        ← TurnFinalizer: 落库 + 发终态事件 + publish turn.completed
├── resumer.py          ← TurnResumer: aborted/partial 消息回滚 streaming + 抓快照
├── orchestrator.py     ← TurnOrchestrator: 新 turn / resume 两条主线 (调 ContextManager)
├── turn_run.py         ← ChatTurnRun: 背景 asyncio.Task + abort_event + 订阅
├── broadcaster.py      ← SSE 实时广播
├── event_store.py      ← ChatEventStore: chat_runs/<message_id>/events.jsonl (断线回放)
├── supervisor.py       ← ChatTurnSupervisor: turn 进程内注册表 + evict + 磁盘清理
└── __init__.py         ← 对外仅暴露 build_turn_orchestrator
```

> 注: 上下文构建已统一走 `context_mgmt.ContextManager`（不再有 `chat/assembler.py` 与
> `forge.context` 双层兼容架构）；LLM 选择由 `ReActRunner.from_profile` + `GatewayBinding` 承接。

## 总体架构

```
┌────────────────────────────────────────────────────────────────────┐
│ api/routes/v1/chat.py  (HTTP / SSE 边界)                            │
│   POST /completions / POST /resume                                 │
│      ↓ 仅做参数解析 + 调下面 + 包 SSE                                │
└─────────────────────────┬──────────────────────────────────────────┘
                          ▼
┌────────────────────────────────────────────────────────────────────┐
│ TurnOrchestrator                                                   │
│   - run_turn(...)       新一轮对话                                  │
│   - resume_turn(...)    继续未完成的 assistant 消息                  │
│                                                                    │
│   按顺序串起 5 个角色, 在合适位置 yield 生命周期事件                  │
└──────────────┬─────────────────────────────────────────────────────┘
       ┌───────┴───────┬────────────────┬─────────────┬─────────────┐
       ▼               ▼                ▼             ▼             ▼
  ┌─────────┐    ┌──────────┐    ┌──────────────┐ ┌────────┐  ┌───────────┐
  │Preparer │    │Resumer   │    │ContextManager│ │Runner  │  │Finalizer  │
  │(new turn│    │(resume   │    │(ctx 构建     │ │(agent  │  │(落库+发终态│
  │ 持久化) │    │ 加载快照)│    │ +压缩)       │ │ 循环)  │  │ +publish) │
  └─────────┘    └──────────┘    └──────────────┘ └────────┘  └───────────┘
```

## 五个角色的契约

| 角色 | DB 接触 | yield SSE 事件 | 输入 | 输出 |
|---|---|---|---|---|
| `TurnPreparer` | ✅ 自有事务 | ❌ | user_id / message | `TurnContext` |
| `TurnResumer` | ✅ 自有事务 | ❌ | user_id / message_id | `(TurnContext, _AgentSnapshot, ResumeState)` |
| `ContextManager` | ✅ 由 Chat build_once 自有事务 | ❌ | `ContextRequest` | `ContextSnapshot` |
| `ReActRunner` | ❌ | ✅ 中间事件 (delta/tool_call/tool_result/...) | messages / abort_event | `RunResult` (累积态) |
| `TurnFinalizer` | ✅ 自有事务 | ✅ 终态事件 (done/error/task_partial) | RunResult / BuildMeta / prev_state? | — |

DB session 由 Chat 的 `build_once` 按需开关. 跟 chat 路由 / fastapi 请求 session 完全解耦, 因为 SSE 流的生命周期超出 fastapi 请求.

## 统一入口 (S6.5 M1)

`POST /api/v1/chat/completions` 是 chat 与 workflow 的统一启动入口。请求体加可选
`workflow` 字段决定走向:

```jsonc
{
  "message": "...",
  // 不传 workflow → 走 chat 路径 (本文 A/B 节描述的 run_turn / resume_turn).
  "workflow": {
    "template_id": "quick_fix",   // 显式跑该模板, 跳过 triage
    // 或
    "mode": "auto",               // triage 决定; 命中 question_only 等"对话级"模板时降级回 chat
    "pause_after_phase": false
  }
}
```

路由层位于 `agent_platform/api/routes/v1/chat.py:chat_completions`,内部通过
`agent_platform/chat/workflow_dispatcher.py:resolve_workflow_route` 计算最终走向:

| `workflow` 字段 | 决策 | SSE 首事件 |
|---|---|---|
| 缺省 / None | chat 路径,行为零变化 | `session_created` |
| `{template_id: "X"}` | workflow 路径,直接跑模板 X | `workflow.started` |
| `{mode: "auto"}` + triage 命中 quick_fix/... | workflow 路径 | `workflow.started` |
| `{mode: "auto"}` + triage 命中 `question_only` | 降级 chat 路径 | `session_created` |

事件协议不引入新类型: chat 路径 SSE 帧与原协议字节级一致,workflow 路径 SSE 帧与
`GET /api/v1/workflows/{id}/events?follow=true` 等价。客户端读首个事件 type 即可分辨走向。

`/api/v1/workflows/{id}/{events|abort|resume|artifacts/*}` 等观察/控制类专属路径不动,
**只统一启动入口**。

---

## 两条主线

### A. 新对话 `run_turn`

```
TurnPreparer.prepare
  ↓ 持久化 user_msg + 占位 assistant_msg, 拿到 assistant_msg_id
yield session_created / session_renamed / message_start
  ↓
ContextManager.build                       ← 拼 [system, ...history, <current_question>]
  ↓
if ThresholdTrigger 命中:                  ← R2 主动压缩
  yield compaction_started
  await SummaryCompaction + build_once     ← SummaryService inline + rebuild
  yield compaction_done
  ↓
build_llm_chain_for_agent                  ← 按 agent.model_id 解析 provider/model
  ↓
runner = _RUNNER_REGISTRY[agent.mode](...)  ← agent mode 分发
  ↓
async for event in runner.run(...)         ← R3-R4 LoopGuards 已挂上
  yield 透传给 SSE
  ↓
Finalizer.finalize → status=done + publish turn.completed
```

### B. 继续生成 `resume_turn` (R6)

```
TurnResumer.prepare
  ↓ 加载 assistant_msg, 校验 status ∈ {aborted, partial}, 抓 prev_state 快照
  ↓ msg.status = "streaming" (允许 /chat/stop 重新挂上 + 拒绝并发 resume)
yield message_resumed (注意: 不是 message_start, 前端不创建新气泡)
  ↓
ContextManager.build(allow_compaction=False) ← ctx.current_user_message = RESUME_PROMPT
  ↓
_inject_partial_into_messages(...)         ← 在末尾 prompt 前插入 partial_assistant + 完成态 tool_results
  ↓ (status="running" 的 tool_call 整体丢弃, 防 LLM 报错)
runner.run                                  ← 跟 A 一致
  ↓
Finalizer.finalize(prev_state=prev)        ← ★ 合并: prev.content + new.content
  ↓ usage 各项相加; tool_calls 累加; reasoning 拼接
  ↓
完成 -> done + publish; 又中断 -> 仍然 task_partial, 可再 resume
```

## SSE 事件协议 (与 web/src/types/index.ts 对齐)

| 事件 | 何时 | payload |
|---|---|---|
| `session_created` | 新会话首条消息 | `{session_id, title}` |
| `session_renamed` | 旧会话首条消息且 title 是"新会话" | `{session_id, title}` |
| `message_start` | 新一轮回复开始 | `{message_id, session_id}` |
| `message_resumed` | resume 时 | `{message_id, session_id, prev_content_len, prev_reason}` |
| `compaction_started` (R2) | 主动压缩开始 | `{reason, estimated_tokens, context_window}` |
| `compaction_done` (R2) | 主动压缩结束 | `{tokens_saved, estimated_tokens, rebuild_count, ok}` |
| `delta` | 文本增量 | `{content}` |
| `reasoning_delta` | 思考链增量 | `{content}` |
| `tool_call` | LLM 决定调用工具 | `{tool_call: {id, tool_name, arguments, status}}` |
| `tool_result` | 工具执行结果 | `{tool_call_id, result, status}` |
| `done` | LLM 真正完成 | `{usage, finish_reason: stop, reasoning_duration_ms}` |
| `task_partial` (R5) | 未完成但可继续 | `{message_id, session_id, reason, content_so_far, tool_calls_so_far, resumable: true}` |
| `error` | 不可恢复错误 | `{message, code?}` |

### `task_partial` 触发的几种 reason

| reason | 触发 | 用户感知 |
|---|---|---|
| `aborted` | 用户 `/chat/stop` | 主动停, 有 [继续生成] 按钮 |
| `partial_steps` | LLM 循环到 max_steps (默认 50) | 罕见, StepSafetyNet 通常已让 LLM 在最后步收尾 |
| `partial_tokens` | 累计 token 超 95% 预算 | 长对话 + 高消耗时 |
| `partial_timeout` | 累计墙钟超 240s | 单 turn 跑太久 |

## ReActAgent ↔ chat 层解耦

`agents/react/agent.py` 不知道 LoopGuard / TurnContext / SSE event 概念. chat 层通过两个机制让它"配合", 又不让它依赖 chat 层:

### 1. `before_step` 通用钩子

ReActAgent.stream 接受 `before_step: Callable[[StepContext], Awaitable[StepDecision]]` 参数:

```python
@dataclass(frozen=True)
class StepContext:
    step_index: int
    max_steps: int
    messages_count: int
    last_step_tool_calls: tuple
    accumulated_usage: dict

@dataclass(frozen=True)
class StepDecision:
    inject_system_messages: list[str]   # 追加到 messages 末尾的 system 文本
    force_text_only: bool                # 这一步用 tool_choice="none"
```

ReActAgent 每步开始前调 `before_step`, 拿 `StepDecision` 执行. **它不知道为什么注入文本 / 为什么强制纯文本**, 只懂 "上层让我这样做".

### 2. ReActRunner 桥接

`chat/runner.py:_make_before_step_bridge` 把多个 LoopGuard 合成一个 `before_step` callback:

```
LoopGuard.before_step(LoopState) → Guidance | None
  Guidance.severity ∈ {hint, warning, force_stop}

aggregate: 多 guard 的 guidance 文本拼到 inject_system_messages
          任一 force_stop → force_text_only=True
```

这样:
- 新增 guard = 新增 chat/guards/xxx.py + 注册到 `_default_guard_factories`
- ReActAgent 代码零改动
- 加 plan-execute 模式 = 新 `PlanExecuteRunner` 类, 自己写 before_step

详见 [chat/guards/README.md](guards/README.md).

## 关键约束 (重构时不要破坏)

1. **5 个角色之间只通过冻结数据类传递**: `TurnContext` / `_AgentSnapshot` / `ResumeState` / `AssembledContext` / `RunResult` 都是 frozen dataclass. 角色之间不互相调用方法.
2. **DB session 每次开关**: 一次 SSE 流可能持续几分钟, fastapi request session 早就关了. 每个 DB 操作必须自己 `async with factory() as db: ...`.
3. **agent mode 通过注册表**: `register_runner("plan_execute")(PlanExecuteRunner)`. Orchestrator 从不 hardcode 模式名.
4. **status 状态机**:
   ```
   streaming ──finalize done────→ done
             ──finalize error───→ error  (终态)
             ──finalize aborted─→ aborted  ──resume──┐
             ──finalize partial─→ partial  ──resume──┤
                                                     ▼
                                                  streaming
   ```
5. **publish turn.completed 仅在 finish_reason="stop"**: aborted/partial/error 都不 publish. 用户继续完成才 publish — 否则记忆摘要等后置任务在错误时机触发.
6. **`_inject_partial_into_messages` 必须丢弃未完成 tool_call**: status=running 的 tool_call 没对应 tool_result, OpenAI / 兼容 provider 会报 400.

## 扩展点

| 需求 | 改哪里 |
|---|---|
| 新增 agent mode (plan-execute / supervisor) | 继承 `AgentRunner` ABC 并实现抽象方法, `@register_runner("xxx")` 注册. orchestrator + 路由零改动 |
| 新增 LoopGuard (eg "禁止某 tool 连调") | 在 `chat/guards/` 加新文件, 注册到 `runner._default_guard_factories`. ReActAgent 零改动 |
| 修改主动压缩触发条件 | `context_mgmt/compaction/trigger/` |
| 修改终态事件协议 | `finalizer._done_event / _error_event / _partial_event` 这一处 |
| 加新 SSE 生命周期事件 (eg "thinking_started") | `orchestrator._lifecycle_events` (或 Runner 内部 yield) |
| 加新触发 publish 的事件 (eg "tool.executed") | Finalizer 加 publish 调用; 业务侧通过 `infrastructure/event_bus` 订阅 |

## 相关文档

- LoopGuard 体系: [chat/guards/README.md](guards/README.md)
- 上下文层: [`context/`](../context/) (ContextBuilder / AssembledContext)
- 记忆系统: [`memory/README.md`](../memory/README.md) (SummaryStore + 主动压缩消费方)
- 事件总线: [`infrastructure/event_bus/README.md`](../infrastructure/event_bus/README.md) (turn.completed publisher)
- 任务队列: [`infrastructure/queue/README.md`](../infrastructure/queue/README.md) (摘要任务 broker)
- ReActAgent: [`agents/react/agent.py`](../agents/react/agent.py) (before_step 钩子的实现方)
