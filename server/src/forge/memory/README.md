# `agent_platform.memory` — 记忆系统

本包负责让 LLM "记得" 之前的对话内容. 对上游 (`context.builder.CompositeContextBuilder`) 只暴露一个统一接口 `MemoryStore`, 由后台 Celery worker 异步写入.

## 为什么需要记忆系统

ContextBuilder 拼上下文的素材有四路: `system + history + summary + facts + current_user`. 其中 `history` 受 token 预算限制, 长对话里早期消息会被丢弃 (记入 `BuildMeta.history_messages_dropped`). 这会导致 "串轮":

- 用户问 "我们之前聊到的那个 RAG 项目, 进度怎么样?" → LLM 看不到 100 轮前的对话 → 编造或反复问
- 用户在不同 session 反复表达过偏好 "我用 Python" → 每个新 session 都重头再来

记忆系统的两条主线分别治这两个症:

| 维度 | 解决的问题 | 实现 |
|---|---|---|
| **session-scoped 摘要** | 长对话历史被 token 预算截断 | LLM 周期性生成摘要, 替代被丢弃的原文 |
| **user-scoped 长期事实** | 跨 session 的个人偏好 / 身份 | 用户手动添加 + LLM 抽取, 语义召回 |

## 模块速览

```
memory/
├── base.py                  ← 对外契约: MemoryStore ABC + Summary / Fact / FactRecallRequest 数据类型 + MemoryStoreError
├── null.py                  ← NullMemoryStore: 关闭记忆系统时的占位 (摘要/事实读全返回空)
├── scope.py                 ← MemoryScope 值对象 (user_id) — 隔离的最小单位
├── composite.py             ← CompositeMemoryStore: 把 SummaryStore + FactStore 组合成 MemoryStore
├── hooks.py                 ← install_memory_hooks(): 订阅 turn.completed, 按 every_n_turns 派发任务
├── policies/                ← 策略层 (ABC + NoOp)
│   ├── conflict.py          ConflictResolver: 写入新事实时与已有冲突的处理
│   └── forgetting.py        ForgettingPolicy: 召回过滤 + 物理 prune 双钩子
├── summary/                 ← 摘要子系统 (增量滚动: 旧摘要 + 水位后新消息喂 LLM 融合)
│   ├── store.py             SummaryStore: MySQL 持久化 (session_id PK, upsert + version++)
│   └── summarizer.py        Summarizer: LLM 驱动的摘要生成 (走独立 model 配置)
├── facts/                   ← 长期事实子系统 (user_facts 表 + int8 量化向量召回)
│   ├── store.py             FactStore: write 经 ConflictResolver / recall 按 user 暴力余弦
│   └── service.py           FactExtractionService: 水位 + LLM 结构化抽取 + build_fact_store 装配
└── tasks/                   ← Celery 任务定义 (autodiscover 入口)
    ├── summarize.py         @shared_task("memory.summarize")
    └── extract_facts.py     @shared_task("memory.extract_facts")
```

## 总体设计

```
┌──────────────────────────────────────────────────────────────────────┐
│ 读路径: ContextBuilder.build()                                        │
│                                                                       │
│   memory_store.get_summary(session_id)        ← session-scoped       │
│   memory_store.recall_facts(FactRecallRequest)← user-scoped          │
│                                                                       │
│   失败一律 raise MemoryStoreError -> ContextBuilder 降级,              │
│   写 BuildMeta.degraded, 不阻断主流程                                  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ CompositeMemoryStore (composite.py)                                  │
│  - get_summary    → SummaryStore.get                                 │
│  - recall_facts   → FactStore.recall (facts.enabled=false 时返回 []) │
└──────────────────────────────────────────────────────────────────────┘

╔══════════════════════════════════════════════════════════════════════╗
║ 写路径 (事件驱动 + 异步任务, 解耦自 chat 流程)                          ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  _stream_chat ─ publish("turn.completed", payload) ──┐                ║
║                                                       │ (in-process)  ║
║                                       ┌───────────────▼─────────┐    ║
║                                       │ memory.hooks (订阅者)    │    ║
║                                       │ - 自查 message count    │    ║
║                                       │ - 满足 every_n_turns →   │    ║
║                                       │   queue.submit(...)     │    ║
║                                       └───────────────┬─────────┘    ║
║                                                       │ (Celery)      ║
║                                       ┌───────────────▼─────────┐    ║
║                                       │ memory.tasks.summarize  │    ║
║                                       │ - load_recent (DB)      │    ║
║                                       │ - Summarizer 调 LLM      │    ║
║                                       │ - SummaryStore.upsert   │    ║
║                                       └─────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════╝
```

### 关键解耦

1. **chat 路由 ↔ memory**: 通过 `EventBus.publish("turn.completed")` 解耦. chat 路由不感知 memory 存在; 想改触发条件 / 加新触发点, 只在 `memory.hooks` 内部改, chat 一行不动.
2. **业务侧 ↔ Celery**: 通过 `TaskQueue.submit(task_name, **kwargs)` 解耦. 业务侧不 import Celery 装饰器, 后期换 Temporal / RQ 时业务零改动.
3. **chat LLM ↔ Summarizer LLM**: 通过 `settings.memory.summarizer.{provider,model}` 独立配置. 摘要可以用便宜的小模型, 对话用主模型.

## 数据契约

### `Summary` (会话级)
| 字段 | 说明 |
|---|---|
| `session_id` | PK; 一个 session 一条 |
| `content` | 摘要正文 (Markdown) |
| `covered_until_message_id` | 摘要覆盖到哪条 message 为止; 再往后是原文 history |
| `token_count` | 摘要本身估算 token, 给预算控制用 |
| `version` | 每次重生成 +1 (原地覆盖, 不留历史) |

### `Fact` (用户级)
| 字段 | 说明 |
|---|---|
| `id` | 雪花 (str) |
| `user_id` | 谁的事实 (唯一隔离维度) |
| `content` | "用户偏好 Python" |
| `source` | `"llm_extracted"` \| `"user_manual"` |
| `score` | 召回时填 (语义相似度); 写入时忽略 |
| `source_session_id` | 来源会话 (溯源, 为将来 "删会话连带遗忘" 留口) |

持久化: `user_facts` 表, 向量复用 int8 对称量化 BLOB 模式 (`forge.utils.vector`),
召回按 user 拉全量暴力余弦 (单用户事实量级小, 不引入向量库); embedder 不可用 /
换模型后旧向量不匹配时回退 recency (score=0). 抽取水位独立存
`fact_extraction_watermarks` 表 (Skip/空抽取也推进, 避免重复送 LLM).

### `MemoryScope` (隔离边界)
**不是策略, 是值对象**. 永远要隔离, 唯一维度是 `user_id`
(server 端不做 workspace / 多租户; 若未来需要, 给值对象添字段 +
Store 查询加过滤即可, 不存在 "换隔离策略实现" 这件事).

## 触发与生命周期

### 触发: 每 `every_n_turns` 轮

```
1 轮 = 1 user + 1 assistant = 2 条消息
触发条件: count_by_session % (every_n_turns * 2) == 0 且 count > 0

例: every_n_turns=10
    第 10 / 20 / 30 ... 轮结束后各派发一次, 旧摘要被新摘要覆盖 (version++)
```

设计取舍: 之前讨论过 "只在 `history_dropped > 0` 才触发", 但这样**第一次溢出的那一轮**就来不及拿到摘要. 改为常态化每 N 轮触发, 摘要永远比历史溢出早.

### 失败处理

| 阶段 | 失败时 | 谁来兜底 |
|---|---|---|
| 事件发布 | publisher 不抛, subscriber 异常被吞 | `InProcessEventBus._safe_call` |
| DB count 查询 | hook 内 try/except, 不 submit | `memory.hooks.on_turn_completed` |
| Celery 派发 | hook 内 try/except, 仅日志 | `memory.hooks.on_turn_completed` |
| LLM 调用 / DB upsert | 任务抛 `_RetryableError` → Celery retry (3 次, 30s 间隔) | `tasks.summarize.summarize_task` |
| 读路径 (get_summary / recall_facts) | 抛 `MemoryStoreError` | ContextBuilder 降级, 写 `BuildMeta.degraded` |

**核心准则**: 写路径任一环节失败, 用户对话**不受影响** (只是这轮没生成摘要); 读路径任一环节失败, 上下文质量**降级** (跳过摘要/事实节, 主流程继续).

## 策略层 (ABC-only, Stage 2 只装 NoOp)

不是所有 "听上去要做的事" 都现在做. 但**接口契约**先定下来, 实现按需扩展.

### `ConflictResolver` (`policies/conflict.py`)

调用点: `FactStore.write()` 内, "新事实 vs 语义相似的已有事实" 比对.

返回 ADT `Resolution = Insert | Replace | Merge | Skip`, Store 用 `match` 分发.
事实层默认装 `ThresholdDedupResolver` (最高相似分 >= `dedup_threshold` 则 Skip).

| 未来实现 | 行为 |
|---|---|
| `LatestWinsResolver` | 相似度 > 0.9 → REPLACE |
| `LLMJudgeResolver` | 让 LLM 决策 (合并 / 跳过 / 替换) |

### `ForgettingPolicy` (`policies/forgetting.py`)

两个钩子点 (同一个 Policy 对象保证一致性):

```python
is_alive(fact, now)     → 召回时过滤, False = 不返回给 ContextBuilder
should_prune(fact, now) → 定时任务 prune, True = 物理删除
```

软遗忘 (`is_alive=False`) 与硬删除 (`should_prune=True`) 是两态: 中间状态 = 用户在设置页还能看到, 但已不进入上下文.

Stage 2 装 `NoForgetting`. Stage 3+ 视需求加 TTL / Decay / Capacity.

## 配置

```yaml
# config/sys_config.yaml
memory:
  enabled: ${MEMORY_ENABLED:true}     # 总开关; false 时 hooks 不订阅
  summarizer:
    provider: ${MEMORY_SUMMARIZER_PROVIDER:}    # 空 -> 复用 llm.provider
    model: ${MEMORY_SUMMARIZER_MODEL:}          # 空 -> 复用 llm.default_model
    history_limit: 100                          # 任务从 DB 最多取多少条
    max_summary_tokens: 1500
  trigger:
    every_n_turns: 10
  facts:                                          # 用户长期事实层
    enabled: ${MEMORY_FACTS_ENABLED:false}        # 代码默认关 (灰度), dev 显式开
    extract_every_n_turns: 5
    top_k: 5
    min_score: 0.5
    dedup_threshold: 0.92
    max_facts_per_turn: 10
```

## 部署要点

1. 装 Celery 依赖: `poetry install -E memory`
2. 启 worker: `celery -A agent_platform.infrastructure.queue.celery_app worker -l info`
3. 建表: `make db-init` (自动建 `session_summaries` 等)
4. 给 summarizer 选小模型: `MEMORY_SUMMARIZER_PROVIDER=deepseek MEMORY_SUMMARIZER_MODEL=deepseek-chat`
5. 想关掉摘要: `MEMORY_ENABLED=false` (服务照常起, NullMemoryStore 兜底)

## 扩展点

| 需求 | 改哪里 |
|---|---|
| 新加触发条件 (eg "每次 user 主动说 '记一下'") | 在 `_stream_chat` 加新 `publish` 调用, 同一事件名或新事件名; `hooks.py` 加 `bus.subscribe` |
| 事实管理 API (设置页增删查) | 复用 `context_mgmt.memory_factory.get_fact_store()` 单例, 新增 `/api/v1/memory/facts` 路由 |
| 真实 conflict 策略 | 新建 `policies/conflict_xxx.py` 继承 ABC, 注入 `FactStore` |
| 真实 forgetting 策略 | 新建 `policies/forgetting_xxx.py`; Celery beat 周期跑 `prune_facts` 任务 |
| 跨进程事件 (web ↔ worker 之间需要互通) | 把 `InProcessEventBus` 换成 `RedisPubSubEventBus`, `get_event_bus()` 内部分发 |

## 相关文档

- 设计讨论原文: [`上下文构建设计.md`](../../../上下文构建设计.md)
- 上下文层: [`agent_platform.context`](../context/) (消费方)
- 事件总线: [`infrastructure/event_bus`](../infrastructure/event_bus/)
- 任务队列: [`infrastructure/queue`](../infrastructure/queue/)
