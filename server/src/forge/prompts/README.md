### 提示词加载时间线

```
服务启动 (FastAPI lifespan)
│
├─ [启动时] PromptRegistry 初始化
│   ├─ 扫描 <repo_root>/prompts/**/*.j2
│   ├─ 逐个编译为 Jinja2 Template 对象
│   └─ 缓存到 _compiled dict，共 3 个模板:
│       • chat/default_system.j2
│       • react/system.j2
│       • memory/summarize.j2
│
└─ [运行时] 每次请求调用 get_registry().render(...)
    ├─ ContextAssembler._render_system_prompt()
    │   → render("chat/default_system", agent_name=..., datetime=...)     # 每次 turn
    ├─ ReActAgent._default_system_prompt()
    │   → render("react/system")                                         # 首次懒加载，后续缓存
    └─ Summarizer 异步任务
        → render("memory/summarize", ...)                                # 摘要任务触发时
```

**关键设计点**：

- `PromptRegistry` 是**启动期一次性扫描编译**（`_eager_compile`），运行时只做 `render()`（变量替换），零 IO 开销
- `init_registry()` 在 `lifespan.py:67-75` 调用，早于 DB/Redis/LLM 等任何依赖，确保后续所有请求能立刻拿到模板
- 初始化失败**不会阻断启动**（捕获异常后走 `_FALLBACK_TEXT` 兜底字符串："你是一个 helpful 的 AI 助手"）
- 不支持热重载，改模板需重启服务（有意为之，版本跟 git 走）


### 变量传值时机：**每次请求，在 `ContextAssembler._render_system_prompt()` 中**

模板中的变量，来源各不相同：

```
prompts/chat/default_system.j2          assembler.py:238-244 传参
┌──────────────────────────────┐        ┌──────────────────────────────────────┐
│ {{ agent_name }}             │ ←────  │ agent.name(已删除)                    │
│ {{ user_name }}              │ ←────  │ ctx.user_name                        │
│ {{ datetime }}               │ ←────  │ datetime.now().strftime(...)         │
│ {{ user_system_prompt }}     │ ←────  │ agent.system_prompt                  │
│ {{ tools }}                  │ ←────  │ [] (硬编码占位, 待工具元数据接入)        │
└──────────────────────────────┘        └──────────────────────────────────────┘
```

### 各变量溯源

```
HTTP 请求
  │  POST /chat/completions  { message, session_id, agent_id }
  │  user = CurrentUser       (JWT 解析 → user.id, user.name)
  ▼
chat.py:65-67
  orchestrator.run_turn(user_id=user.id, user_name=user.name, body=...)
  │
  ▼
orchestrator.py:100-108
  ctx, agent_snapshot = preparer.prepare(
      user_id=user_id,
      user_name=user_name,    ← 来自 JWT CurrentUser.name
      ...
  )
  │
  ├─▸ ctx.user_name = user_name          ← 路由透传
  │
  └─▸ agent_snapshot = _AgentSnapshot(
         name       = agent_orm.name,     ← DB agent 表
         system_prompt = agent_orm.system_prompt,  ← DB agent 表
         model_id   = agent_orm.model_id, ← DB agent 表
         ...
     )
  │
  ▼
orchestrator.py:124-126
  build_result, system_prompt = assembler.assemble(ctx, agent_snapshot)
  │
  ▼
assembler.py:_render_system_prompt()
  │
  │  ★ 在这里填充变量 ★
  │
  get_registry().render("chat/default_system",
      user_system_prompt = agent.system_prompt,   ← DB agent.system_prompt
      agent_name         = agent.name,            ← DB agent.name
      user_name          = ctx.user_name,         ← JWT user.name
      datetime           = datetime.now(),        ← 系统时间
      tools              = [],                    ← 硬编码空列表
  )
  │
  ▼
jinja2 模板引擎替换 {{ }} → 返回最终 system prompt 字符串
```

### 关键点

- **`agent.name` / `agent.system_prompt`**：来自 **DB `agents` 表**，在 `TurnPreparer.prepare()` 中查询并打包进 `_AgentSnapshot`
- **`user_name`**：来自 **JWT token**，`api/dependencies.py` 中 `CurrentUser` 依赖注入解析，经路由层 → orchestrator → preparer → TurnContext → assembler
- **`datetime`**：**请求时刻**的系统时间（非模板编译时），每次 render 都取最新值
- **`tools`**：当前硬编码 `[]`，CLA.md 注明 "待工具元数据统一访问点" 后接入
- **`user_system_prompt`**：以**纯文本变量**注入，不会被 Jinja2 二次解析（`autoescape=False` + 直接作为变量值，防模板注入）