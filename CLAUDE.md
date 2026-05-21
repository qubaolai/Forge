# Forge

输入目标，炼出产物。——将用户的自然语言需求，经由动态规划、多 Agent 协作、隔离执行、合并验证，产出可落地的结构化成果。

## 技术栈

- **后端**：Python FastAPI（`server/`）
- **前端**：React（`web/`）
- **CLI**：独立 Python 包，Rich + prompt_toolkit（`cli/`）
- **LLM 模型**：fast=sonnet-4-6, smart=sonnet-4-6, strong=opus-4-7

## 顶层目录

```
forge/
├── server/     # Python FastAPI 后端（主体）
├── cli/        # 独立 CLI 包
├── web/        # React 前端
├── docs/       # 设计文档
└── README.md
```

## server 内部结构

- `server/src/forge/adaptive/` — 核心新增模块：models, planner, validator, scheduler, executor, orchestrator, integrator, verifier, workspace, store, events
- `server/src/forge/agents/` — Agent 执行层（从 assistant 迁移，不动）
- `server/src/forge/chat/` — TurnOrchestrator 等对话引擎（保留，被 executor 复用）
- `server/src/forge/llm/`, `context/`, `memory/`, `retrieval/`, `tools/`, `guardrails/`, `prompts/`, `observability/`, `infrastructure/`, `utils/` — 从 assistant 直接复制
- `server/src/forge/api/` — 路由、中间件、schemas
- `server/src/forge/config/` — 配置，含 sys_config.yaml

## 核心概念

### ModeRouter
三路路由：`chat`（TurnOrchestrator，不动）、`simple`（单 Agent 任务）、`adaptive`（完整 adaptive 流程）。入口在 `/api/v1/chat/completions`，由 `mode` 参数控制。

### AdaptiveRun（7 步主流程）
1. **DISCOVER** — READ-only ReActAgent 探索代码库 → DiscoveryReport
2. **PLAN** — Planner LLM 生成 TaskGraph JSON（tool_use 强制结构化输出）
3. **VALIDATE** — TaskGraphValidator 校验 8 条规则（DAG 无环、tools 在 allowlist、write_scope 在 root_path 下、kind-tools 一致性等），失败则 replan
4. **EXECUTE** — Scheduler 按 wave 并行执行 TaskNode；每个 TaskNode 在隔离环境（git worktree）中运行，产出 PatchSet artifact
5. **INTEGRATE** — 合并所有 PatchSet，处理文本冲突和语义冲突
6. **VERIFY** — 质量门禁（lint/test/type-check），失败则生成修复 task 回 Step 4
7. **CLOSE** — FinalReport，标记 COMPLETED

### RunStatus 状态机
`CREATED → PLANNING → VALIDATING → EXECUTING → INTEGRATING → VERIFYING → COMPLETED`，可能转向 `FAILED` / `BLOCKED` / `ABORTED`

### TaskGraph / TaskNode
- **TaskNode**：id, title, kind(READ/WRITE/EXECUTE/REVIEW/INTEGRATE), allowed_tools, read_scope, write_scope, deps, model_profile, max_steps, acceptance_criteria, output_contract
- **TaskGraph**：nodes dict，planner 原始输出
- **TaskStatus**：PENDING → RUNNING → COMPLETED / FAILED / SKIPPED

### Artifact 类型
`discovery_report | task_graph | patch_set | review_report | test_report | integration_report | conflict_report | final_report`

### 写隔离
- `IsolateStrategy` Protocol：prepare / collect / cleanup
- `GitWorktreeStrategy`：唯一实现，要求 workspace 是 git repo，每个 write task 在 `/tmp/forge-{run_id}-{task_id}` 创建独立 worktree

### TaskOptions 合并链
`sys_config.default_options < TaskOptionsIn（用户传入）< HardCaps（硬上限）`

## API 端点

**保留（复用）**：`POST /api/v1/chat/completions`, `POST /api/v1/chat/resume`, `POST /api/v1/chat/stop`, sessions, kb CRUD

**新增**：`/api/v1/runs`（CRUD）, `/api/v1/runs/{id}/events`（SSE）, `/api/v1/runs/{id}/abort`, `/api/v1/runs/{id}/decide`, `/api/v1/artifacts`（查询）

**废弃**：`/api/v1/workflows/*`（不迁移）

**Schema 变更**：`ChatCompletionIn` 新增 `mode: "auto" | "chat" | "task"` 和 `task_options: TaskOptionsIn`，废弃 `workflow`

## 设计红线（不可逾越）

1. **Planner 只能声明资源，不能执行**：TaskGraph 的 allowed_tools 必须过 Validator，超出 allowlist 直接 reject
2. **write_scope 路径必须在 workspace root_path 下**：Validator + executor 双重校验
3. **READ kind task 不能有写工具**：kind="read" + write_scope 非空 = Validator 拒绝
4. **write task 必须走写隔离**：writer_mode=isolated_worktree 时只允许写 worktree
5. **TurnOrchestrator 接口不变**：executor 通过 RunTurnOverrides 驱动，不修改 TurnOrchestrator 签名
6. **聊天路径零退化**：mode=chat 或 auto 路由为 chat 时走原路径，adaptive 层完全不参与

## 实现阶段

| Phase | 目标 | 关键依赖 |
|-------|------|----------|
| M0 | 项目脚手架、代码迁移、CI | — |
| M1 | ModeRouter + TaskOptions + schema 变更 | M0 |
| M2 | AdaptiveRun 状态机 + store + events | M0 |
| M3 | API 骨架（runs, artifacts, SSE, chat 接 ModeRouter） | M1, M2 |
| M4 | Planner + Validator（LLM tool_use 输出 TaskGraph + 8 条校验规则） | M2 |
| M5 | 串行 Executor（无隔离），全流程 3-node 跑通 | M3, M4 |
| M6 | 写隔离（GitWorktreeStrategy），PatchSet 产出 | M5 |
| M7 | Integrator + 冲突处理 | M6 |
| M8 | Verifier + Replan 循环 | M7 |
| M9 | 并行 Scheduler（wave 计算 + asyncio.gather） | M8 |
| M10 | CLI（forge_cli 包，run/chat/attach/runs 命令，Rich 看板） | M3 |
| M11 | Web 前端（从 assistant-web 迁移，RunsPage + TaskGraph 视图） | M3 |

## 编码约定

- Python 代码使用 `forge` 作为顶层包名
- 数据模型使用 `@dataclass`（不可变）或 Pydantic `BaseModel`（API 层）
- 异步 IO 为主，executor 使用 `asyncio.gather` 并行
- 配置通过 `sys_config.yaml` + 环境变量覆盖
- 测试放在 `server/tests/`，按模块组织
- Jinja2 模板在 `server/prompts/`，按功能分子目录
