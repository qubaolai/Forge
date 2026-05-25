# Forge 阶段开发与验收标准 (v3)

> **最终目标**：通用的 Adaptive Task Runtime。**runtime 不预设任何领域**——领域知识来自三处：(1) LLM Planner 的世界知识，(2) 用户的自然语言目标，(3) 可选的 YAML profile。
>
> 本文档分两部分：
> - **第〇章** 定义最终架构（必读）
> - **M0–M12** 给出落地路径

**当前进度**：M0 ✅ + M1 部分完成；M2 起为待开发。

---

# 第〇章：最终目标架构

## 0.1 系统定位

Forge 是**一个目标驱动的元运行时（meta-runtime）**。

类比：

| 系统 | 通用原语 | 领域知识来源 |
|---|---|---|
| Unix | shell + 文件 + pipe | shell 脚本（数据） |
| Forge | Tools + ContentTypes + Strategies | TaskGraph by LLM + YAML profile（数据） |

Forge 自身**不知道**什么是"软件开发"或"研究"。它只知道：
- 怎么调度一张 DAG
- 怎么调用工具
- 怎么让 LLM 按 prompt 干活
- 怎么隔离写入
- 怎么按 content_type 合并产物
- 怎么按策略验证

"软件开发"是 Planner LLM + 一份 YAML profile（可选）+ 用户目标共同涌现的结果，不是 runtime 代码里写死的概念。

## 0.2 三层架构

```
┌────────────────────────────────────────────────────────────────┐
│  Layer 3 — Domain Knowledge（运行时 / 可选）                     │
│  • LLM Planner 的世界知识（最主要的领域知识来源）                │
│  • 用户自然语言目标                                              │
│  • 可选 YAML profile（提供默认值、约束、风格提示）               │
│  • 不写 Python，纯数据                                           │
└────────────────────────────────────────────────────────────────┘
                              ↓ 作为参数喂给
┌────────────────────────────────────────────────────────────────┐
│  Layer 2 — Adaptive Runtime（编排核心 / 通用）                   │
│  • AdaptiveRunOrchestrator（7 步状态机）                         │
│  • Planner（LLM → TaskGraph）/ Validator / Scheduler / Executor  │
│  • Integrator / Verifier（通过 StrategyRegistry 派发）            │
│  • Store / EventBus / IsolateStrategy                            │
│  • 完全不感知"软件开发""研究"这类概念                            │
└────────────────────────────────────────────────────────────────┘
                              ↓ 通过三张表查询
┌────────────────────────────────────────────────────────────────┐
│  Layer 1 — Universal Primitives（原语 / 通用）                   │
│  • ToolRegistry           — 所有可调工具（read/write/shell/llm) │
│  • ContentTypeRegistry    — MIME 风格的产物类型 + JSON Schema   │
│  • StrategyRegistry       — 按 content_type 派发 integrator /   │
│                             verifier / isolate_strategy          │
└────────────────────────────────────────────────────────────────┘
```

**关键事实**：runtime 代码中**不会**出现以下符号：
- ❌ `software_development` / `research` / `writing` 等 domain 名
- ❌ `DomainPack` / `CapabilityProfile` / `ArtifactContract` 等"领域抽象类"
- ❌ `PatchSet` / `ResearchBrief` 等领域专属类型

替换为：
- ✅ `ContentType("text/x-patch")` / `ContentType("text/markdown")` / `ContentType("application/json", schema=...)`
- ✅ `Tool("shell")` / `Tool("write_file")`
- ✅ `Strategy` 按 content_type 派发

## 0.3 核心数据模型

### TaskNode（无领域字段）
```python
@dataclass(frozen=True)
class TaskNode:
    id: str
    title: str
    goal: str                              # 自然语言子目标，由 Planner 写

    # 执行配置（Planner 即兴组合，非预定义 capability）
    tools: tuple[str, ...]                 # 引用 ToolRegistry 的 id
    model_profile: Literal["fast","smart","strong"]
    system_prompt: str                     # Planner 当场生成 / Jinja 渲染
    max_steps: int

    # 输入输出（用 content_type 描述，非枚举）
    input_artifact_ids: tuple[str, ...]    # 消费哪些 artifact
    output_content_type: str               # 产出什么 content_type
    output_schema_ref: str | None          # 可选 JSON Schema ID

    # 资源边界
    deps: tuple[str, ...]
    read_scope: tuple[str, ...]
    write_scope: tuple[str, ...]

    # 运行时状态
    status: TaskStatus = TaskStatus.PENDING
    artifact_ids: tuple[str, ...] = ()
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

**关键变化**：
- 没有 `capability_id`（运行时即兴）
- 没有 `kind: READ/WRITE/...`（由 `tools` 和 `write_scope` 共同推断）
- 没有 `output_contract: PATCH_SET/...`（用 `output_content_type` 描述）

### Artifact（MIME 风格）
```python
@dataclass(frozen=True)
class Artifact:
    id: str
    run_id: str
    producer_task_id: str

    content_type: str                      # MIME 风格
    schema_ref: str | None                 # 可选 JSON Schema ID
    payload: Any                           # 可序列化（dict/str/bytes 经 base64）

    metadata: dict                         # 自由字段（base_ref / source_url / tags 等）
    created_at: datetime
    size_bytes: int
```

**content_type 的命名**（参考 IANA / RFC 6838）：
- `text/markdown`（通用文本）
- `text/x-patch`（unified diff）
- `application/json`（结构化数据，配 schema_ref）
- `application/vnd.forge.task-graph+json`
- `application/vnd.forge.discovery-report+json`
- `text/x-mermaid`（Mermaid 图）
- `image/svg+xml`
- `application/x.run-log`（流式日志快照）
- 用户可自定义任意 `application/x.{whatever}` 子类型

### Tool / ContentType / Strategy（三张表）
```python
# ToolRegistry：所有可调工具
class ToolRegistry:
    def register(self, tool_id: str, impl: Tool) -> None: ...
    def get(self, tool_id: str) -> Tool: ...
    def list_allowed(self, allowlist: tuple[str,...]) -> dict[str, Tool]: ...

# ContentTypeRegistry：产物类型 + 可选 JSON Schema
class ContentTypeRegistry:
    def register(
        self,
        content_type: str,
        schema: dict | None = None,
        description: str = "",
    ) -> None: ...
    def validate_payload(self, content_type: str, payload: Any) -> None: ...
    def list(self) -> list[ContentTypeInfo]: ...

# StrategyRegistry：按 content_type 派发 integrator / verifier / isolation
class StrategyRegistry:
    def register_integrator(self, content_type: str, impl: Integrator) -> None: ...
    def register_verifier(self, content_type: str, impl: Verifier) -> None: ...
    def register_isolator(self, scope_pattern: str, impl: IsolateStrategy) -> None: ...

    def get_integrator(self, content_type: str) -> Integrator: ...
    def get_verifier(self, content_type: str) -> Verifier: ...
    def get_isolator(self, write_scope: tuple[str,...]) -> IsolateStrategy: ...
```

### Profile（可选 / YAML / 纯数据）
```yaml
# 任何团队/项目可放在 ~/.forge/profiles/ 或 workspace 下
# 完全可选 —— 不提供 profile 时系统用全局默认值

name: software_development
description: Build/modify software projects

# 提示 Planner 这次 run 的领域风格
planner_hints: |
  This workspace is a Python codebase. Prefer:
  - typed signatures
  - small, testable functions
  - test files alongside implementation

# 默认隔离策略
isolation:
  default: git_worktree           # StrategyRegistry 的 key
  fallback: patch_only

# 默认 verifier
verifier:
  type: shell
  cmd: "pytest && ruff check ."
  timeout_sec: 600

# 工具白名单（运行时收紧 ToolRegistry 的全集）
tool_allowlist:
  - read_file
  - write_file
  - edit_file
  - list_directory
  - grep
  - glob_search
  - run_tests
  - git_ops

# 预期产出 content_types（用于 Planner 提示，非强约束）
expected_output_content_types:
  - text/x-patch
  - application/vnd.forge.test-report+json

# HITL 策略：控制 Planner / Worker / 关键边界 多敢问用户
interaction_policy:
  planner: collaborative          # autonomous | collaborative | guided
  worker:  autonomous             # 执行期默认不打扰用户
  approval_gates:                 # 强制 BLOCKED 等用户 /decide 的阶段
    - after_plan                  # 把 TaskGraph 给用户审一遍
    - before_integrate            # 合并前最后确认
  # 可选 ↓
  integration_conflict: guided    # 冲突时必问（默认就是 BLOCKED + /decide）
```

**Profile 的本质**：一份 hint，告诉 Planner "这次任务大概是什么风格，倾向用哪些工具，最终产出长什么样，多敢问用户"。**完全可省略**。

### 不带 profile 也能跑
```bash
forge run "把我桌面的 csv 文件按月份合并成一个" --workspace ~/Desktop
```
没有 profile 时：
- Planner 用全 ToolRegistry + 默认 model
- `interaction_policy = collaborative`（Planner 判断模糊度自己决定问不问）
- Validator 用 hard caps 和通用规则兜底
- Integrator 根据产出的 content_type 自动派发（`text/csv` 走 `csv_concat`，`text/x-patch` 走 `git_apply` 等）
- Verifier 默认跳过（除非 LLM 自己决定加一个 verify task）

## 0.4 设计哲学

| 维度 | 传统 workflow_template | DomainPack 路线（v2） | **通用 runtime（v3）** |
|---|---|---|---|
| 加新领域 | 写 YAML 模板 | 写 Python class + 注册 | **写 0 行代码**（可选写 YAML profile） |
| 领域知识在哪 | 模板文件 | DomainPack 类 | **LLM + 用户目标 + 可选 profile** |
| Artifact 类型 | 模板枚举 | 领域注册的 contracts | **MIME content_type + JSON Schema** |
| Integrator | 模板写死 | 领域类实现 | **按 content_type 派发的策略** |
| 扩展边界 | 模板能力 | 注册的 domain 集合 | **LLM 的认知边界** |

**最关键的一句话**：Forge 不预设"能干什么"，只预设"怎么干"——怎么调度、怎么隔离、怎么合并、怎么验证。

## 0.5 HITL 是一等公民，不是异常处理

设计哲学的关键转向：**人在回路（Human-in-the-Loop）不是"出错时才介入"的 escape hatch，而是 Agent 自己判断"我没把握"时主动求助的常态能力。**

### 0.5.1 错误模型 vs 正确模型

| 错误模型（避免） | 正确模型（采用） |
|---|---|
| 用户给目标 → 系统一路自动跑 → 失败才 BLOCKED 问人 | 用户给目标 → Planner agent 拿不准 → 主动 `ask_user(...)` → 沟通清楚再继续 |
| HITL 是 escape hatch（仅 BLOCKED 时触发） | HITL 是一等公民工具（任何 agent 任何阶段都能调） |
| 用户说一句模糊话题，系统就开始随意搜索处理 | Planner 先判断"够不够清楚"，不够就先问 |

### 0.5.2 实现机制：`ask_user` 是通用 Tool

`ask_user` 注册在 ToolRegistry 里，地位等同于 `read_file` / `write_file`：

```python
# adaptive/tools/ask_user.py
class AskUserTool:
    name = "ask_user"
    description = "Ask the user a clarifying question and wait for response"
    schema = {
        "type": "object",
        "required": ["question"],
        "properties": {
            "question": {"type": "string"},
            "options": {                      # 可选：让用户从选项里选
                "type": "array",
                "items": {"type": "string"},
            },
            "context": {                      # 可选：解释为什么问
                "type": "string",
            },
        },
    }

    async def call(self, params: dict, ctx: ToolContext) -> ToolResult:
        # 1. 把问题作为 SSE 事件发出
        await ctx.events.publish("clarification_request", {
            "task_id": ctx.task_id,
            "question": params["question"],
            "options": params.get("options"),
            "context": params.get("context"),
        })
        # 2. run status → AWAITING_USER (子状态：哪个 task 在等)
        await ctx.store.set_awaiting_user(ctx.run_id, ctx.task_id)
        # 3. 阻塞等待 POST /runs/{id}/respond
        response = await ctx.user_response_future
        return ToolResult(success=True, output=response)
```

### 0.5.3 Planner 是 Agent，不是函数

v3 关键改动：Planner 从"一次性 LLM 函数"升级为"adaptive agent"：

```
旧（v2/v3 早期）：
  goal + discovery → LLM.complete() → TaskGraph

新（v3 当前）：
  Planner agent 启动，allowed_tools = [ask_user, read_file, grep, ...]
  ↓
  Planner LLM 判断：信息够不够？
    够 → 输出 TaskGraph，结束
    不够 → 调 ask_user → 等响应 → 再判断
  ↓
  可能多轮 ask_user / 读代码 / 再问 → 最终 TaskGraph
```

Planner 的判断由 system_prompt 注入的 `interaction_policy` 决定：
- `autonomous` — 永不调 ask_user，模糊时用 LLM 最佳猜测
- `collaborative`（默认）— 模糊度高就问，自己掂量
- `guided` — 阶段边界都问（接近"严格 HITL"流程）

### 0.5.4 Worker（执行期 Agent）也能调 ask_user

不只是 Planner——EXECUTE 阶段的任何 TaskNode agent，如果发现需要决策（如"这个字段应该叫 amount 还是 total"），也能调 ask_user。

由 profile/options 控制是否开放：

```yaml
# profile
interaction_policy:
  planner: collaborative
  worker: autonomous            # 执行期不再骚扰用户
  integrate_conflict: guided    # 合并冲突必问
```

### 0.5.5 接下来跑你那两个场景会变什么样

**场景 A 小说大纲**：
1. 用户说"帮我写小说" → Planner 调 `ask_user`："悬疑/言情/科幻？字数预期？目标读者？"
2. 用户回答 → Planner 觉得够了 → 输出 `[t1 大纲, t2 第一章]` 的 TaskGraph
3. （可选 approval gate）展示 TaskGraph，确认后执行

**场景 B 增加开票功能**：
1. 用户说"加开票功能" → Planner 先扫一眼 workspace（READ 工具）
2. Planner 调 `ask_user`：
   ```
   看到你已有 Order 表，开票方案我有两种思路：
     A) 独立 Invoice 表，与 Order 一对多
     B) 在 Order 加字段 + 触发器生成
   另外几个问题：
     - 需要 PDF 导出吗？
     - 多税率支持？
     - 是否对接外部税务接口？
   ```
3. 用户回答 → Planner 可能继续读代码 / 再问 / 直到输出 TaskGraph
4. EXECUTE 中某个 task 发现 schema 不清楚，可以再调 ask_user（如果 worker policy 允许）

## 0.6 完整 7 步状态机

### 0.6.1 主流程（happy path）

```
CREATED
   │
   ▼
PLANNING (Planner agent 启动) ──────┐
   │                                │
   │  可暂态: ◀──── AWAITING_USER ◀─┤  (Planner 调 ask_user)
   │           ────────────────────▶│
   │                                │
   ▼
VALIDATING (TaskGraphValidator)
   │                                ▲
   │  失败 → replan_count++ ────────┘ (≤ max_replans 回 PLANNING)
   │  超限 → BLOCKED
   ▼
EXECUTING (Scheduler 按 wave 跑)
   │
   │  可暂态: ◀──── AWAITING_USER ◀──┐  (Worker 调 ask_user)
   │           ────────────────────▶│
   │                                │
   ▼
INTEGRATING (按 content_type 派发 Integrator)
   │
   │  冲突 → CONFLICT_REPORT artifact → BLOCKED (人工 decide)
   │                                       │
   │  ◀────────────── /decide ─────────────┘
   ▼
VERIFYING (按 profile.verifier 派发 Verifier)
   │
   │  失败 → 生成修复 task → replan_count++ → 回 EXECUTING
   │  超限 → BLOCKED
   ▼
COMPLETED
```

### 0.6.2 横切状态（任意阶段可触发）

```
任意阶段 ─→ AWAITING_USER   (active agent 调了 ask_user，等用户响应)
任意阶段 ─→ ABORTED         (用户主动 /abort)
任意阶段 ─→ FAILED          (不可恢复错误：LLM 完全不可用、磁盘满等)
```

### 0.6.3 BLOCKED 的几种子原因

`BLOCKED` 状态承载多种"需要人决策才能继续"的情况，用 `block_reason` 字段区分：

| block_reason | 触发场景 | 用户决策方式 |
|---|---|---|
| `replan_exceeded` | VALIDATE/VERIFY 重 replan 超限 | `/decide` action=`abort`/`force_continue` |
| `integration_conflict` | 多 PatchSet 同一文件同一区域冲突 | `/decide` action=`manual_resolve`/`abort` |
| `approval_required` | profile 在阶段边界设置了 approval_gate | `/decide` action=`approve`/`reject` |
| `policy_violation` | 越界写、越权调用工具等硬规则触发 | `/decide` action=`abort`（一般不允许继续） |

> **注意**：`AWAITING_USER` ≠ `BLOCKED`。
> - `AWAITING_USER`：agent 主动问问题，正常对话流程，用 `/respond` 回答
> - `BLOCKED`：流程被卡住需要决策，用 `/decide` 决策

### 0.6.4 RunStatus 枚举（更新）

```python
class RunStatus(str, Enum):
    CREATED         = "created"
    PLANNING        = "planning"
    VALIDATING      = "validating"
    EXECUTING       = "executing"
    INTEGRATING     = "integrating"
    VERIFYING       = "verifying"
    COMPLETED       = "completed"
    FAILED          = "failed"
    ABORTED         = "aborted"
    BLOCKED         = "blocked"          # 需要 /decide
    AWAITING_USER   = "awaiting_user"    # 需要 /respond，新增
```

### 0.6.5 事件流（SSE）的核心事件

```
run_created
phase_started      { phase: "planning" | "executing" | ... }
phase_completed
task_started       { task_id, title }
task_completed     { task_id, artifact_ids }
task_failed        { task_id, error }
wave_started       { wave_index, task_ids }
wave_completed
artifact_created   { artifact_id, content_type }
clarification_request  { task_id, question, options?, context? }   # 新增
user_responded     { task_id, response }                            # 新增
blocked            { reason, payload }
unblocked          { decision }
replan_triggered   { reason, replan_count }
run_completed      { final_artifact_ids }
run_failed         { error }
run_aborted
```

---

# 第一章：进度总览

| Phase | 主题 | 状态 |
|---|---|---|
| M0 | 项目脚手架 + 代码迁移 | ✅ 完成 |
| M1 | ModeRouter + TaskOptions + Schema | 🟡 60% |
| M2 | AdaptiveRun 状态机 + Store + ORM | ⬜ 待开始 |
| **M3** | **Universal Primitives Layer**（Tool/ContentType/Strategy Registry） | ⬜ 待开始 |
| M4 | API 骨架（runs / artifacts / SSE） | ⬜ 待开始 |
| M5 | Planner + Validator + compute_waves() | ⬜ 待开始 |
| M6 | 串行 Executor + `patch_only` 策略 | ⬜ 待开始 |
| M7 | `git_worktree` 策略 + 多 Isolator 派发 | ⬜ 待开始 |
| M8 | Integrator 策略集合（按 content_type 派发） | ⬜ 待开始 |
| M9 | Verifier 策略集合 + Replan 循环 | ⬜ 待开始 |
| M10 | 并行 Scheduler | ⬜ 待开始 |
| M11 | CLI（forge_cli 包） | ⬜ 待开始 |
| M12 | Web 前端 | ⬜ 待开始 |
| (附) | `profiles/software_development.yaml`（示例，非阶段） | 与 M5/M8/M9 同步 |

---

# 第二章：阶段定义

## M0 — 项目脚手架 + 代码迁移 ✅

### 验收（已完成）
- [x] `poetry install` 成功
- [x] `make dev` 启动成功
- [x] `pytest server/tests/unit/` 全绿
- [x] forge 包结构完整

---

## M1 — ModeRouter + TaskOptions + Schema 🟡 60%

### 目标
`/api/v1/chat/completions` 通过 `mode` 三路路由；声明 `TaskOptions` 合并链。

### 剩余工作
- ⬜ `adaptive/mode_router.py` — `ModeRouter.route() -> "chat"|"simple"|"adaptive"`
- ⬜ `sys_config.yaml` 加 `task_execution` 段（hard_caps、default_options、profiles_dir）
- ⬜ `config/domains/task_execution.py` — Settings 模型

### 验收标准
- [x] 默认 mode=auto 走 TurnOrchestrator
- [x] mode=chat 强制聊天路径
- [x] mode=task 当前返回 stub
- [ ] `ModeRouter.route()` 单测
- [ ] `TaskOptions.build()` 三层合并单测
- [ ] `sys_config.task_execution` 字段解析正确

---

## M2 — AdaptiveRun 状态机 + Store + ORM ⬜

### 目标
让 `AdaptiveRun` 和 `Artifact` 可持久化、可事件流回放。

### 交付物
- `adaptive/store.py`：
  - `AdaptiveRunStore` Protocol（内存 + MySQL 两实现）
  - `create_run / get_run / update_status / append_artifact / list_artifacts`
  - `append_event(run_id, event) / iter_events(run_id, after_seq=0)`
- ORM：`adaptive_run_orm.py` / `artifact_orm.py` / `run_event_orm.py`
- Repository：`adaptive_run_repo.py` / `artifact_repo.py`

### 数据库 Schema
```
adaptive_runs       (run_id PK, owner_user_id, workspace_path, goal,
                     profile_name nullable, status, task_graph_json,
                     current_wave, replan_count, metadata_json,
                     created_at, updated_at)

artifacts           (artifact_id PK, run_id FK, producer_task_id,
                     content_type, schema_ref nullable,
                     payload_json, metadata_json, size_bytes, created_at)
                     -- index(run_id, content_type)

run_events          (id PK, run_id FK, seq, event_type, payload_json, ts)
                     -- unique index(run_id, seq)
```

注意：`profile_name` 是 nullable —— **不传 profile 也能跑**。

### 验收标准
- [ ] 状态机迁移：合法 + 非法路径全单测
- [ ] `create_run` 持久化后 `get_run` 完全恢复
- [ ] `task_graph` JSON 序列化往返字段完整
- [ ] `append_event` + `iter_events(after_seq=N)` 可重放
- [ ] 内存 + MySQL 两版本通过同一组合同测试

### 红线
- ⚠️ Artifact 表用 JSON 列存 payload
- ⚠️ 事件 seq 严格递增、唯一
- ⚠️ Store 接口**禁止** `update_artifact`

---

## M3 — Universal Primitives Layer ⬜ 【核心抽象层】

### 目标
建立 runtime 的三张通用表：**ToolRegistry / ContentTypeRegistry / StrategyRegistry**。M3 完成后，所有"领域知识"都在数据 / Profile / LLM 里，**runtime 代码完全 domain-agnostic**。

### 交付物

#### A. `adaptive/registry/tool_registry.py`
```python
class Tool(Protocol):
    id: str
    description: str
    schema: dict                          # JSON Schema for params
    async def call(self, params: dict, context: ToolContext) -> ToolResult: ...

class ToolRegistry:
    def register(self, tool: Tool) -> None
    def get(self, tool_id: str) -> Tool
    def filter_allowed(self, allowlist: tuple[str,...]) -> dict[str, Tool]
    def all_ids(self) -> tuple[str, ...]
```
注册启动时调一次。复用 `forge/tools/` 现有工具集，包成 Tool Protocol。

**内置必备 Tool（M3 必须实现）**：
- `ask_user(question, options?, context?)` —— HITL 一等公民，触发 SSE `clarification_request` + status=AWAITING_USER，阻塞等 `/respond`
- `read_file` / `write_file` / `edit_file` —— 包装现有 fs 工具
- `list_directory` / `glob_search` / `grep` —— 包装现有探索工具
- `shell` —— 执行 shell 命令（受 write_scope 约束）

`ask_user` 的实现见 0.5.2 节。

#### B. `adaptive/registry/content_type_registry.py`
```python
class ContentTypeRegistry:
    def register(
        self,
        content_type: str,
        schema: dict | None = None,
        description: str = "",
        binary: bool = False,
    ) -> None
    def get(self, content_type: str) -> ContentTypeInfo
    def validate_payload(self, content_type: str, payload: Any) -> None
    def known_types(self) -> list[str]
    def supports(self, content_type: str) -> bool   # 未注册也可以用，但无 schema 校验
```

启动时预注册常用类型（无 schema 也可，留空）：
- `text/markdown`、`text/plain`、`text/csv`、`text/x-patch`
- `application/json`、`application/x.run-log`
- `application/vnd.forge.task-graph+json`（自带 schema）
- `application/vnd.forge.discovery-report+json`（自带 schema）
- `image/svg+xml`、`image/png`
- 用户自定义 `application/x.<anything>` 自动注册无 schema 版本

#### C. `adaptive/registry/strategy_registry.py`
```python
class Integrator(Protocol):
    async def merge(self, artifacts: list[Artifact],
                    workspace_path: str) -> IntegrationResult: ...

class Verifier(Protocol):
    async def verify(self, artifact: Artifact,
                     workspace_path: str, criteria: dict) -> VerificationResult: ...

class IsolateStrategy(Protocol):
    async def prepare(self, node: TaskNode, run: AdaptiveRun) -> IsolatedEnv: ...
    async def collect(self, env: IsolatedEnv) -> Artifact: ...
    async def cleanup(self, env: IsolatedEnv) -> None: ...

class StrategyRegistry:
    # Integrator 按 content_type 派发（"*" 是兜底）
    def register_integrator(self, content_type: str, impl: Integrator) -> None
    def get_integrator(self, content_type: str) -> Integrator

    # Verifier 按命名 type 派发（profile / planner 选）
    def register_verifier(self, name: str, impl: Verifier) -> None
    def get_verifier(self, name: str) -> Verifier

    # Isolator 按命名 strategy 派发
    def register_isolator(self, name: str, impl: IsolateStrategy) -> None
    def get_isolator(self, name: str) -> IsolateStrategy
```

#### D. `adaptive/profile/loader.py`（profile 加载 / 可选）
```python
@dataclass(frozen=True)
class InteractionPolicy:
    planner: Literal["autonomous","collaborative","guided"] = "collaborative"
    worker:  Literal["autonomous","collaborative","guided"] = "autonomous"
    approval_gates: tuple[str, ...] = ()   # 如 ("after_plan", "before_integrate")
    integration_conflict: Literal["autonomous","collaborative","guided"] = "guided"

@dataclass(frozen=True)
class Profile:
    name: str
    planner_hints: str = ""
    isolation: IsolationConfig = field(default_factory=...)
    verifier: VerifierConfig | None = None
    tool_allowlist: tuple[str, ...] | None = None   # None = 全集
    expected_output_content_types: tuple[str, ...] = ()
    interaction_policy: InteractionPolicy = field(default_factory=InteractionPolicy)

class ProfileLoader:
    def load_by_name(self, name: str) -> Profile      # 从 profiles_dir 读 yaml
    def load_inline(self, raw: dict) -> Profile        # 直接传 dict
    def default(self) -> Profile                       # 全空 profile，全开放
```

#### E. 内置兜底策略（M3 阶段最少实现一组）
- `StrategyRegistry.register_integrator("*", PassthroughIntegrator())` —— 兜底：直接把多个 artifact 列在 IntegrationReport 里，不真合并
- `StrategyRegistry.register_verifier("noop", NoopVerifier())` —— 默认不验证
- `StrategyRegistry.register_isolator("none", NoIsolation())` —— 兜底：无隔离（仅 M3 占位，M6 起被禁）

具体的 git_apply / shell verifier / git_worktree 不在 M3 实现（分散到 M6/M7/M8/M9）。

### 验收标准
- [ ] `ToolRegistry.register(tool)` + `get(id)` 单测
- [ ] **`ask_user` Tool 单测**：调用 → 发 SSE 事件 → 设 AWAITING_USER → mock `/respond` 注入 → 拿到响应继续
- [ ] `ContentTypeRegistry.validate_payload` 对带 schema 的 type 走 jsonschema 校验；无 schema 的 type 直接通过
- [ ] `StrategyRegistry` 三种派发各有单测；找不到时回落到 `"*"` 兜底
- [ ] `ProfileLoader.default()` 返回空 profile，所有字段都有合理默认；`interaction_policy.planner=collaborative` 默认值正确
- [ ] **DSL 自描述测试**：列出已注册 tools / content_types / strategies，**返回结果完全是数据**，不含任何 `software_development` 字样
- [ ] 注册一个**完全虚构**的 content_type（`application/x.foobar`）+ 一个虚构 integrator + 跑一遍策略派发，证明扩展不需改 runtime

### 红线
- ⚠️ Runtime 代码（`adaptive/orchestrator.py` 等）**禁止** import 任何领域特定模块；不允许出现字符串字面量如 `"software_development"`、`"patch_set"`、`"test_report"` 等
- ⚠️ 所有 Tool / ContentType / Strategy 通过 Registry 拿，不能跨模块直接 import 具体实现
- ⚠️ 没有 `kind: PATCH_SET` 这样的枚举；只有 `content_type: "text/x-patch"` 这样的字符串

---

## M4 — API 骨架（runs / artifacts / SSE） ⬜

### 目标
HTTP/SSE 暴露 M2 + M3；`chat` 路由真正接入 ModeRouter。

### 交付物
- `api/routes/v1/runs.py`：
  - `POST /runs` — body: `{goal, workspace_path, profile_name?, profile_inline?, task_options?, interaction_policy?}`
  - `GET /runs` / `GET /runs/{id}`
  - `POST /runs/{id}/abort` — 用户主动中止
  - `POST /runs/{id}/decide` — body `{action, payload?}` 用于 BLOCKED 决策
  - **`POST /runs/{id}/respond`** — body `{task_id, response}` 用于 AWAITING_USER 响应 ask_user
  - `GET /runs/{id}/events` — SSE，支持 `?after_seq=N`
- `api/routes/v1/artifacts.py`：
  - `GET /artifacts?run_id=...&content_type=...&task_id=...`
  - `GET /artifacts/{id}`
- `api/routes/v1/registry.py`（新）：
  - `GET /registry/tools` — 列出所有 Tool
  - `GET /registry/content-types` — 列出 ContentType + schema
  - `GET /registry/strategies` — 列出 integrator/verifier/isolator
  - `GET /registry/profiles` — 列出可用 profile 文件
- `api/services/adaptive_run_service.py`
- `api/routes/router.py` 挂载新路由
- `api/routes/v1/chat.py` 的 mode=task 不再 stub，创建 run + 桥接事件流（含 clarification_request）

### 关键 SSE 事件（M4 必须支持）
```json
// 普通进度
data: {"type": "task_started", "task_id": "t1", "title": "..."}
data: {"type": "task_completed", "task_id": "t1", "artifact_ids": ["art_abc"]}

// AWAITING_USER 触发
data: {"type": "clarification_request", "task_id": "t1",
       "question": "...", "options": null, "context": "..."}

// 用户响应注入后
data: {"type": "user_responded", "task_id": "t1", "response": "..."}

// BLOCKED
data: {"type": "blocked", "reason": "integration_conflict", "payload": {...}}
```

### 验收标准
- [ ] 创建 run → SSE 订阅 → abort 全链路
- [ ] `?after_seq=N` 续传
- [ ] 多用户隔离
- [ ] `GET /registry/*` 返回的内容里**没有任何"软件开发"字样**，全是通用原语
- [ ] `POST /runs` 不传 `profile_name` 也能成功（用 default profile）
- [ ] **AWAITING_USER 流程 e2e**：mock 一个 task 调 ask_user → SSE 推 clarification_request → `POST /respond` → SSE 推 user_responded → run 继续

---

## M5 — Planner Agent + Validator + compute_waves() ⬜

### 目标
**Planner 是 agent，不是一次性 LLM 函数**——它能调 `ask_user` 跟用户对话补足信息，能调 `read_file/grep` 探索 workspace，最终输出 TaskGraph。Validator 兜底硬门禁；compute_waves 纯函数先就位。

### 交付物
- `adaptive/planner.py`：
  - `Planner` 是 adaptive agent，不是函数：
    - `tools = (ask_user, read_file, list_directory, grep, glob_search, finalize_plan)`
    - 输出契约：`output_content_type = "application/vnd.forge.task-graph+json"`
    - 终止条件：agent 调 `finalize_plan(task_graph)` 这个特殊 tool
    - 默认 `interaction_policy.planner` 决定调 ask_user 的尺度
  - LLM 看到的可用资源 = 当前 ToolRegistry + ContentTypeRegistry 的简介（注入到 system_prompt）
  - 最终输出的 TaskGraph JSON 示例（同前）：
    ```json
    {
      "nodes": [
        {
          "id": "t1",
          "title": "...",
          "goal": "<natural language sub-goal>",
          "tools": ["read_file", "write_file", "ask_user"],
          "model_profile": "smart",
          "system_prompt": "<inline system prompt written by planner>",
          "max_steps": 25,
          "input_artifact_ids": [],
          "output_content_type": "text/x-patch",
          "output_schema_ref": null,
          "deps": [],
          "read_scope": ["${workspace_path}/src"],
          "write_scope": ["${workspace_path}/src"]
        }
      ]
    }
    ```
- Planner 系统 prompt：`prompts/adaptive/planner.j2`
  - 通用模板，不含任何 domain 字面量
  - 注入：当前 ToolRegistry / ContentTypeRegistry / profile.planner_hints / interaction_policy
  - 关键指令："如果用户目标含义不清，先调 ask_user 澄清，不要凭想象规划"
- `adaptive/validator.py`：
  - `TaskGraphValidator.validate(graph, tool_registry, content_type_registry, profile, options) -> ValidationResult`
  - 9 条规则（见下）
- `adaptive/scheduler.py`（compute_waves 部分）：
  - `compute_waves(graph) -> list[list[task_id]]` 纯函数
- `agents/discovery/` 复用现有 ReActAgent —— READ-only（DISCOVER 阶段产出 discovery_report）

### 9 条 Validator 规则（全部 domain-agnostic）
1. **DAG 无环**：拓扑排序
2. **deps 存在性**：每个 `deps` 引用在图内
3. **tools 在白名单**：`node.tools ⊆ tool_registry` 且（如果 profile 有 allowlist）`⊆ profile.tool_allowlist`
4. **write_scope 在 workspace_path 下**：路径前缀校验
5. **write/read 语义一致性**：`write_scope` 非空 → tools 必须含至少一个写工具；没有写工具 → `write_scope` 必须为空
6. **同 wave 写冲突**：用 `compute_waves` 结果检查，同 wave 内 write_scope 不重叠
7. **output_content_type 已注册**：在 ContentTypeRegistry 内；或符合 `application/x.{anything}` 模式（开放扩展）
8. **资源上限**：`max_steps ≤ hard_caps`、`len(nodes) ≤ max_agents × max_replans`
9. **input_artifact_ids 已声明**：所有引用的 artifact_id 必须由前序 task 产出（按 deps 推断）

### 验收标准
- [ ] Planner 对 fixture goal（信息充足）一次输出可解析 TaskGraph
- [ ] **Planner 多轮对话测试**：fixture goal 故意模糊（"帮我搞一下这个项目"）→ Planner 应调 ask_user → mock 用户响应 → Planner 继续 → 多轮后输出 TaskGraph
- [ ] `interaction_policy.planner=autonomous` → Planner 不调 ask_user，凭猜测出 TaskGraph
- [ ] `interaction_policy.planner=guided` → Planner 几乎每步都 ask
- [ ] LLM 输出非合法 JSON → `PlannerSchemaError` → 触发 replan
- [ ] Validator 每条规则正例 + 反例（共 18+ 用例）
- [ ] `compute_waves` 覆盖：无依赖 / 链式 / 写冲突 / 复杂 DAG
- [ ] **planner.j2 模板里没有任何领域字面量**——只描述通用原语
- [ ] 把 `tool_registry` 换成只含 1 个虚构工具 → Planner 应该尝试用这个工具规划 → 证明 Planner 受 registry 驱动

### 红线
- ⚠️ Planner 输出**当场写 system_prompt**，不依赖"已注册 capability"
- ⚠️ Planner 是 agent，要能多轮 ask_user，不是一次性 LLM 调用
- ⚠️ Validator 是硬门禁
- ⚠️ Planner prompt 通用，**不识别任何 domain 名字**

---

## M6 — 串行 Executor + `patch_only` 策略 ⬜

### 目标
跑通主流程前 4 步。Worker 不直接写主 workspace。

### 交付物
- `adaptive/orchestrator.py` — `AdaptiveRunOrchestrator.run(goal, profile, options)`
- `adaptive/executor.py`：
  - `TaskExecutor.run(node, run, artifact_lookup, isolate_strategy) -> Artifact`
  - 通过 `RunTurnOverrides` 调 `TurnOrchestrator.run_turn`
- `adaptive/workspace/patch_only.py`：
  - `PatchOnlyStrategy` 实现 `IsolateStrategy`
  - `prepare`: 创建虚拟文件树（OverlayFS / in-memory）
  - worker 工具写到该层，**不**碰主 workspace
  - `collect`: 把变更包装成 `content_type=text/x-patch` artifact（payload = unified diff string）
  - `cleanup`: 删临时层
- `StrategyRegistry.register_isolator("patch_only", PatchOnlyStrategy())`
- `chat/types.py` 加 `RunTurnOverrides`

### 验收标准
- [ ] e2e：`POST /runs` + goal="新建 hello.py 打印 Hello"
  - run 完成后主 workspace **保持干净**
  - artifacts 含 `content_type=text/x-patch` 的 PatchSet
- [ ] 完整事件序列可见
- [ ] TurnOrchestrator 签名零修改（grep 验证）
- [ ] 聊天路径回归全绿
- [ ] **红线测试**：worker 试图写 `${workspace_path}/...` → 工具层拒绝
- [ ] 把 isolate_strategy 换成 `"none"` 兜底 → 任意写操作直接拒绝（M6 起 "none" 在 production 默认禁用）

### 红线
- ⚠️ Worker 绝不直接写主 workspace
- ⚠️ TaskNode 的 tools 由 Planner 决定，executor 不增不减

---

## M7 — `git_worktree` 策略 + 多 Isolator 派发 ⬜

### 目标
注册 `git_worktree` 策略，让 PatchOnly 升级到真正的 worktree；profile 可选择 isolator。

### 交付物
- `adaptive/workspace/git_worktree.py`：
  - `GitWorktreeStrategy` 实现 `IsolateStrategy`
  - prepare: `git worktree add /tmp/forge-{run_id}-{task_id} HEAD`
  - collect: `git diff base_ref` 包装成 `content_type=text/x-patch` artifact，metadata 含 `base_ref`
  - cleanup: `git worktree remove --force`
- `StrategyRegistry.register_isolator("git_worktree", ...)`
- profile.isolation.default 决定用哪个；`workspace_path` 非 git repo 时自动 fallback 到 `patch_only`

### 验收标准
- [ ] PatchOnly 和 GitWorktree 通过**同一组**合同测试
- [ ] 非 git 项目自动降级 + WARN
- [ ] worktree 清理在 finally
- [ ] Artifact 都用 `content_type=text/x-patch`，executor 无需感知具体策略
- [ ] **可注册一个第三方虚构策略**（`InMemorySandbox`），证明派发系统通用

### 红线
- ⚠️ executor 只调 `IsolateStrategy` 接口，不查"是 worktree 还是 patch_only"
- ⚠️ `git worktree remove --force` 永远在 finally

---

## M8 — Integrator 策略集合（按 content_type 派发） ⬜

### 目标
注册多个 Integrator，runtime 通过 `StrategyRegistry.get_integrator(content_type)` 派发。**runtime 不知道 git patch 是什么，只调 integrator.merge()**。

### 交付物
- `adaptive/integrators/`：
  - `git_patch.py` — `GitPatchIntegrator`：注册到 `text/x-patch`
  - `text_concat.py` — `TextConcatIntegrator`：注册到 `text/markdown`、`text/plain`
  - `json_merge.py` — `JsonMergeIntegrator`：注册到 `application/json`
  - `passthrough.py` — `PassthroughIntegrator`：注册到 `"*"` 兜底（M3 已有）
- 启动时注册到 `StrategyRegistry`
- orchestrator INTEGRATE 阶段按产出的 content_type 分组 → 对每组调对应 integrator

### 验收标准
- [ ] PatchSet 两 task 改不同文件 / 同文件不同区域 / 同文件同区域，全单测
- [ ] Markdown concat 顺序稳定
- [ ] JSON merge 深合并 + 冲突报告
- [ ] 注册一个虚构 integrator（`application/x.custom-merge`）→ 跑通
- [ ] BLOCKED：冲突时产出 `content_type=application/vnd.forge.conflict-report+json` artifact
- [ ] `/runs/{id}/decide` 提交决策推进流程

### 红线
- ⚠️ Runtime 不感知具体合并语义
- ⚠️ 冲突也是 artifact，不是异常 / 内部对象

---

## M9 — Verifier 策略集合 + Replan 循环 ⬜

### 目标
注册多个 Verifier；profile 选用哪个；失败触发 replan。

### 交付物
- `adaptive/verifiers/`：
  - `shell_cmd.py` — `ShellCommandVerifier`：执行 shell 命令
  - `llm_judge.py` — `LLMJudgeVerifier`：LLM 当裁判
  - `schema_check.py` — `SchemaVerifier`：JSON Schema 校验产出
  - `noop.py` — `NoopVerifier`：兜底（M3 已有）
- 启动时注册
- orchestrator VERIFY 阶段：
  - 看 profile.verifier 配置 → 拿对应 verifier
  - 失败 → 生成修复 task → replan_count++ → 回 EXECUTE
- `prompts/adaptive/verifier_replan.j2`（通用模板，把失败原因喂回 Planner）

### 验收标准
- [ ] 三种 verifier 各单测
- [ ] verifier 失败 1 次 → 修复 → 成功 → COMPLETED
- [ ] 超 `max_replans` → BLOCKED
- [ ] profile.verifier=None → 跳过 verify
- [ ] verifier 超时 → FAILED
- [ ] 注册虚构 verifier → 跑通

---

## M10 — 并行 Scheduler ⬜

### 目标
M5 的 compute_waves 已就位，加上 `asyncio.gather` 真并行 + `Semaphore` 限流。

### 验收标准
- [ ] 无依赖 3 个 task 写不同文件 → 同 wave 并行
- [ ] 写冲突 task 按 compute_waves 自动分 wave
- [ ] 单 task 失败：同 wave 其他继续；下 wave 依赖 SKIPPED
- [ ] `max_agents=4` 限并发：5 个 task 分两 wave (4+1)
- [ ] 性能：3 个 1s task 并行 ≤ 1.3s

---

## M11 — CLI（forge_cli 包） ⬜

### 目标
独立 Python 包，HTTP/SSE 连接 server。

### 交付物
- `cli/pyproject.toml`
- `cli/src/forge_cli/`：
  - `main.py` — Typer
  - `commands/run.py / chat.py / attach.py / runs.py / artifact.py / profiles.py / registry.py`
  - `renderer/dashboard.py` / `plain.py` / `hitl.py`

### 验收标准
- [ ] `forge run "需求" --workspace ~/code/x [--profile software_development]`
  - **不传 `--profile` 也能跑**
- [ ] `forge profiles list / show <name>`
- [ ] `forge registry tools / content-types / strategies`
- [ ] `forge runs --status running`
- [ ] `forge attach run_xxx`
- [ ] CI/非 tty → plain renderer JSONL
- [ ] BLOCKED → `[c]ontinue / [a]bort / [m]anual`

---

## M12 — Web 前端 ⬜

### 目标
RunsPage + TaskGraph + ArtifactViewer（按 content_type 自动选展示组件）。

### 验收标准
- [ ] RunsPage 分页 + status 过滤
- [ ] TaskGraph 实时更新 + 按状态着色
- [ ] ArtifactViewer 按 content_type 派发：
  - `text/x-patch` → diff viewer
  - `text/markdown` → markdown render
  - `application/json` → 树形 JSON viewer
  - `image/svg+xml` → 直接渲染
  - `*` → raw text
- [ ] 创建 run 时可选 profile（下拉来自 `/registry/profiles`）
- [ ] BLOCKED 弹决策框
- [ ] chat 路径零回归

---

# 第三章：跨阶段红线

| # | 红线 | 验证方式 |
|---|---|---|
| R1 | Runtime 代码不含任何 domain 字面量 | `grep -r 'software_development\|patch_set\|research_brief' src/forge/adaptive/` 返回 0 行 |
| R2 | TaskNode 没有 `kind`/`capability_id`/`output_contract` 这些预设字段 | TaskNode 字段单测 |
| R3 | Artifact 用 content_type 描述类型 | 数据模型审查 |
| R4 | 所有 Integrator/Verifier/Isolator 通过 Registry 派发 | grep 调用方式：禁止 `from forge.adaptive.integrators.git_patch import ...` 直接 import |
| R5 | Planner 当场生成 system_prompt | TaskNode 必含非空 system_prompt 字段 |
| R6 | Worker 永远不直接写主 workspace（M6 起） | PatchOnly + WorktreeStrategy 单测 + 拦截测试 |
| R7 | TurnOrchestrator 签名零修改 | git diff vs M0 基线 |
| R8 | 聊天路径零退化 | mode=chat 回归套件 |
| R9 | Artifact 不可变 | Store 无 update_artifact API |
| R10 | 事件 seq 严格递增 | run_events 唯一索引 + 单测 |
| R11 | Task 间通信只通过 Artifact | 设计审查 + grep 反模式 |
| R12 | Profile 可选 | 不传 profile_name 也能创建 run |
| R13 | 注册新 content_type / integrator / verifier 不需修改 runtime | M3/M8/M9 注册式单测 |
| R14 | `ask_user` 是一等公民 Tool | 在 ToolRegistry 内，任何 agent 都能调；M3 单测覆盖 |
| R15 | HITL ≠ 异常处理 | AWAITING_USER 是正常状态而非 BLOCKED 子类；用 /respond 而非 /decide |
| R16 | Planner 是 agent，不是函数 | Planner 能多轮 ask_user，M5 多轮对话测试 |

---

# 第四章：测试策略

### 单元测试
- 每个 `adaptive/*.py` ≥80% 行覆盖
- 状态机迁移：合法/非法路径全覆盖
- Validator：每条规则正例 + 反例
- compute_waves：覆盖无依赖 / 链式 / 写冲突 / 复杂 DAG
- 三张 Registry：注册、查找、派发、兜底

### 通用性测试（M3 必加）
- **虚构 content_type 测试**：注册 `application/x.foobar` + 虚构 integrator/verifier，跑通完整 run，证明 runtime 不感知具体领域
- **空 profile 测试**：不传任何 profile，给 LLM Planner 一个目标，跑完整流程

### 合同测试
- Store 接口的内存版 + MySQL 版同一组测试
- IsolateStrategy 接口的 PatchOnly + GitWorktree 同一组测试

### 回归测试
- 每阶段末跑 `pytest server/tests/`
- chat 路径单独回归套件

### 端到端
- M6 末：fake-LLM 跑 "新建 hello.py"
- M8 末：真 LLM + software_development profile 跑 "为 demo 加函数 + 单测"
- M9 末：故意写错 → verifier 失败 → replan → 修好
- **M11 末**：真 LLM **不传任何 profile** 跑一个非软件目标（例："读这个 CSV 算月均值并产出报告"），证明通用性

---

# 第五章：推荐推进节奏

```
本周    : M1 收尾
+1 周   : M2 完成
+2 周   : M3 完成（核心抽象层）⭐
+3 周   : M4 完成（API） + M5 启动
+4 周   : M5 完成（Planner + Validator + compute_waves）
+5 周   : M6 完成（patch_only 跑通端到端）⭐ 最大里程碑
+6 周   : M7 + M8（worktree + integrators）
+7 周   : M9 + M10（verifiers + 并行）
+8 周   : M11（CLI）/ M12（Web，可并行）+ profile 完善
```

**关键提示**：
- **M2 是数据地基**，store 接口稳定前不进 M4
- **M3 是抽象地基**，三张 Registry 接口稳定前不进 M5
- **M6 是第一个端到端里程碑**

---

# 附录 A：`profiles/software_development.yaml`（示例，非阶段）

```yaml
name: software_development
description: Build/modify software projects

planner_hints: |
  This workspace is a software project.
  Prefer small, testable changes. Always add tests for new functions.
  Use git_worktree isolation. Run pytest + ruff after changes.

isolation:
  default: git_worktree
  fallback: patch_only

verifier:
  type: shell_cmd
  params:
    cmd: "pytest && ruff check ."
    timeout_sec: 600

tool_allowlist:
  - read_file
  - write_file
  - edit_file
  - list_directory
  - grep
  - glob_search
  - run_tests
  - git_ops

expected_output_content_types:
  - text/x-patch
  - application/vnd.forge.test-report+json
```

这份 YAML 不在 `src/forge/` 里，而在 `server/profiles/` 或用户 `~/.forge/profiles/`，**runtime 不依赖它存在**。

# 附录 B：其它 profile 示例（仅示意通用性，不开发）

```yaml
# profiles/research.yaml
name: research
planner_hints: "Web research with citations. Cross-check 2+ sources per claim."
isolation:
  default: none                  # 研究任务不写文件
verifier:
  type: llm_judge
  params:
    criteria: "All factual claims must cite source URL"
tool_allowlist:
  - http_request
  - knowledge_search
  - write_file
expected_output_content_types:
  - text/markdown
  - application/vnd.forge.citation-set+json
```

```yaml
# profiles/diagram.yaml
name: diagram
planner_hints: "Produce architecture diagrams as Mermaid or SVG."
isolation:
  default: patch_only
verifier:
  type: schema_check
  params:
    schema_ref: forge:mermaid-validator
tool_allowlist:
  - read_file
  - write_file
expected_output_content_types:
  - text/x-mermaid
  - image/svg+xml
```

**关键事实**：这些 profile 都是**数据**。加 / 改 / 删它们**不需要碰一行 Python**。Runtime 不知道有它们存在，只知道"profile.tool_allowlist 限制 tool 集合""profile.verifier 决定用哪个 verifier"等通用规则。

---

# 附录 C：与 v2 (DomainPack 路线) 的对照

| v2 概念 | v3 替代物 |
|---|---|
| `DomainPack` | 一份 YAML profile + Registry 注册 |
| `CapabilityProfile.id="code.write_python"` | Planner 当场写 system_prompt + 选 tools |
| `ArtifactContract.id="patch_set"` | `content_type="text/x-patch"` + 可选 JSON Schema |
| `DomainPackRegistry` | ToolRegistry + ContentTypeRegistry + StrategyRegistry |
| `pack.integrator_class` | `StrategyRegistry.get_integrator(content_type)` |
| `pack.verifier_class` | `StrategyRegistry.get_verifier(profile.verifier.type)` |
| `producer_capabilities` 引用闭包 | content_type 自由组合，开放扩展 |
| 加领域 = 写 Python 类 | 加领域 = 写 YAML（甚至不需要） |
