# `forge` — 后端模块总览

Forge 后端：以 **Web Chat + LLM 网关 + RAG 检索底座** 为核心。旧 CLI 远程执行路径已经删除，未来 CLI 作为胖客户端在本地运行 agent loop，并通过服务端 LLM 网关访问模型。

> 顶层架构见仓库根 [`CLAUDE.md`](../../../CLAUDE.md) 与 [`docs/architecture.md`](../../../docs/architecture.md)。本文件是各模块 README 的索引。

## 模块地图

| 模块 | 职责 | 文档 |
|------|------|------|
| **agents** | Web Chat 使用的 Agent 内核（ReActAgent）+ lifecycle guard 扩展 | [agents/README](agents/README.md) |
| **chat** | Chat turn 业务编排（preparer→context→runner→finalizer + SSE） | [chat/README](chat/README.md) · [guards](chat/guards/README.md) |
| **context_mgmt** | 统一上下文构建（Fork-Join）+ 压缩 + digest + 召回 | [context_mgmt/README](context_mgmt/README.md) |
| **llm** | LLMGateway（Pre/Post pipeline + dispatcher + provider 适配） | [llm/README](llm/README.md) |
| **tools** | Web Chat 只读工具（knowledge_search / time_tool / read_message）基类、注册与执行 | [tools/README](tools/README.md) |
| **memory** | 会话摘要 + 长期记忆（读路径同步 / 写路径事件驱动） | [memory/README](memory/README.md) |
| **retrieval** | RAG：解析/切分/向量化/存储/检索 | [retrieval/README](retrieval/README.md) |
| **guardrails** | 工具护栏 + 合规审计（input/output 为预留框架） | [guardrails/README](guardrails/README.md) |
| **api** | FastAPI 路由 / 依赖 / 中间件 / lifespan 装配 | [api/README](api/README.md) |
| **infrastructure** | DB / 缓存 / 队列 / 事件总线 / JSONL / 文件存储 | [infrastructure/README](infrastructure/README.md) · [event_bus](infrastructure/event_bus/README.md) · [queue](infrastructure/queue/README.md) |
| **config** | 配置加载 + 域模型（含 agent_profiles） | [config/README](config/README.md) |
| **observability** | 日志 / 追踪 / 指标 / 成本预算 | [observability/README](observability/README.md) |
| **quota** | 用户滚动窗口用量额度 | [quota/README](quota/README.md) |
| **prompts** | Prompt 模板注册与渲染 | [prompts/README](prompts/README.md) |
| **core** | 跨层公共原语（类型/异常/响应/上下文/加密） | [core/README](core/README.md) |
| **utils** | 无业务语义的纯工具（雪花 ID 等） | [utils/README](utils/README.md) |

## 服务端执行路径

| | Chat 路径（Web） |
|---|---|
| 入口 | `POST /api/v1/chat/*` |
| 编排 | `TurnOrchestrator` + `ChatTurnRun`（背景 task） |
| 持久化 | `chat_messages` DB + `chat_runs/*/events.jsonl` |
| 上下文 | `context_mgmt.ContextManager` |
| lifecycle | `[GuardLifecycleAdapter]` |

## 阅读顺序（新人）

1. `chat/README` → Chat 主链怎么跑
2. `agents/README` → ReActAgent 与 lifecycle guard
3. `context_mgmt/README` + `llm/README` + `tools/README` → 能力层
4. `infrastructure/README` + `observability/README` → 设施与可观测
