# `forge` — 后端模块总览

Forge 后端：以**单一 `ReActAgent` 内核 + 可组合 `AgentLifecycle` 扩展**承载所有智能体执行，并存 **Chat（Web）** 与 **CLI（plan_exec / workflow）** 两条执行路径。

> 顶层架构见仓库根 [`CLAUDE.md`](../../../CLAUDE.md) 与 [`docs/architecture.md`](../../../docs/architecture.md)。本文件是各模块 README 的索引。

## 模块地图

| 模块 | 职责 | 文档 |
|------|------|------|
| **agents** | Agent 内核（ReActAgent）+ 扩展机制（AgentLifecycle）+ HITL + CLI 编排 + 角色 | [agents/README](agents/README.md) · [roles](agents/roles/README.md) |
| **chat** | Chat turn 业务编排（preparer→context→runner→finalizer + SSE） | [chat/README](chat/README.md) · [guards](chat/guards/README.md) |
| **context_mgmt** | 统一上下文构建（Fork-Join）+ 压缩 + digest + 召回 | [context_mgmt/README](context_mgmt/README.md) |
| **llm** | LLMGateway（Pre/Post pipeline + dispatcher + provider 适配） | [llm/README](llm/README.md) |
| **tools** | 工具基类 / 注册 / 执行 + guardrail | [tools/README](tools/README.md) |
| **memory** | 会话摘要 + 长期记忆（读路径同步 / 写路径事件驱动） | [memory/README](memory/README.md) |
| **retrieval** | RAG：解析/切分/向量化/存储/检索 | [retrieval/README](retrieval/README.md) |
| **guardrails** | 工具护栏 + 合规审计（input/output 为预留框架） | [guardrails/README](guardrails/README.md) |
| **api** | FastAPI 路由 / 依赖 / 中间件 / lifespan 装配 | [api/README](api/README.md) |
| **infrastructure** | DB / 缓存 / 队列 / 事件总线 / JSONL / RunStore / 文件存储 | [infrastructure/README](infrastructure/README.md) · [event_bus](infrastructure/event_bus/README.md) · [queue](infrastructure/queue/README.md) |
| **config** | 配置加载 + 域模型（含 agent_profiles） | [config/README](config/README.md) |
| **observability** | 日志 / 追踪 / 指标 / 成本预算 | [observability/README](observability/README.md) |
| **quota** | 用户滚动窗口用量额度 | [quota/README](quota/README.md) |
| **workspace** | CLI 工作区加载 + 工具路径策略 | [workspace/README](workspace/README.md) |
| **prompts** | Prompt 模板注册与渲染 | [prompts/README](prompts/README.md) |
| **core** | 跨层公共原语（类型/异常/响应/上下文/加密） | [core/README](core/README.md) |
| **utils** | 无业务语义的纯工具（雪花 ID 等） | [utils/README](utils/README.md) |

## 两条执行路径（一眼对照）

| | Chat 路径（Web） | CLI 路径（plan_exec / workflow） |
|---|---|---|
| 入口 | `POST /api/v1/chat/*` | `POST /api/v1/runs*` + `/decisions*` |
| 编排 | `TurnOrchestrator` + `ChatTurnRun`（背景 task） | `RunOrchestrator` + `RunSupervisor` |
| 持久化 | `chat_messages` DB + `chat_runs/*/events.jsonl` | `RunStore`（`runs/<id>/` JSONL） |
| 上下文 | `context_mgmt.ContextManager` | 直接渲染 system prompt（暂不走 builder） |
| lifecycle | `[GuardLifecycleAdapter]` | `[Guards, PlanMode/Workflow, Persistence]` |

## 阅读顺序（新人）

1. `agents/README` → 理解内核与扩展机制（架构的钥匙）
2. `chat/README` → Chat 主链怎么跑
3. `context_mgmt/README` + `llm/README` + `tools/README` → 能力层
4. `agents/README`（CLI 段）+ `config/README` → CLI 路径与「配置即治理」
5. `infrastructure/README` + `observability/README` → 设施与可观测
