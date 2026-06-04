# `forge.api` — HTTP / SSE 边界层

FastAPI 应用装配 + 路由 + 依赖注入 + 中间件。这一层只做「HTTP 边界」：参数解析、鉴权、调业务、包响应/SSE，不写业务逻辑。

## 设计理念

1. **薄边界**：路由函数尽量短（chat 路由 ~100 行），业务下沉到 `chat/` `agents/` 等模块。例如 `/chat/completions` 只负责建 TurnRun + 返回 SSE 订阅流。
2. **启动装配集中在 lifespan**：所有运行时组件（DB / 缓存 / LLMGateway / RAG / 后台 loop）的依赖图都在 `lifespan.py` 一处显式装配，顺序即依赖顺序。
3. **fail-fast 启动校验**：`agent_profiles` 等关键配置在启动期强校验，不过直接拒绝启动。
4. **统一响应/异常**：成功走 `core.response.success`，异常由 ErrorHandler 中间件统一转标准错误体。

## 模块速览

```
api/
├── server.py        ← create app: CORS + 中间件 + 挂 /api 路由
├── lifespan.py      ← 启动/关停装配 (组件依赖图的唯一权威)
├── dependencies.py  ← FastAPI 依赖 (AuthenticatedUser / get_db 等)
├── routes/          ← router.py 聚合 + v1/* 各业务域路由
├── schemas/         ← 请求/响应 pydantic 模型
├── services/        ← 路由与领域之间的应用服务 (admin_model / kb_ingest ...)
└── middleware/      ← ClientType / Tracing / ErrorHandler
```

## 启动装配顺序（`lifespan.py`，即组件依赖图）

```
Logging/Tracing → PromptRegistry → ToolRegistry + AGENT_ROLES
  → load_profiles_at_startup (7 项强校验)
  → Database → TaskQueue → EventBus + memory hooks → Redis + ModelConfigCache
  → LLMGateway → RAG 组件 → DecisionRegistry.cleanup_loop → ChatTurnSupervisor.cleanup
```

## 如何使用

业务前缀统一 `/api/v1/*`。新增一个接口：

1. 在 `schemas/` 定义入参/出参模型。
2. 在 `routes/v1/<域>.py` 写路由函数（用 `AuthenticatedUser` 依赖鉴权，复杂逻辑调 `services/` 或领域模块）。
3. 在 `routes/router.py` 挂载该路由。
4. DB 访问用依赖 `get_db`（请求级事务）。

## 如何扩展

- **新业务域**：建 `routes/v1/<域>.py` + 在 `router.py` include。
- **新中间件**：实现后在 `server.py` 注册（注意顺序）。
- **新启动组件**：在 `lifespan.py` 按依赖位置插入装配，关停段对称清理。

## 边界与注意

- `documents.py` / `retrieval.py` / `feedback.py` 路由文件存在但未在 `router.py` 挂载（占位/未启用）。
- HITL 决策统一 `POST /v1/decisions/{token}`，旧 `/runs/{id}/decide` 已废弃。
- SSE 三类：chat（broadcaster 实时 + events.jsonl 回放）、CLI run（cursor 轮询）、admin。
