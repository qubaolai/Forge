# 计划：会话内容存储抽象 + 引用化 Digest 系统

> ⚠️ **2026-06-11 服务端瘦身后的修订说明**：本计划中所有 CLI / RunStore 相关工作项已失效——
> 旧 CLI 执行路径（RunStore / artifact 工具 / `RunStoreContentStore` / `[ref:art:<id>]`）已随服务端瘦身整体删除,
> 服务端只保留 Web Chat 路径。chat 侧（`DbMessageContentStore` / `[ref:msg:<id>]` / `read_message` 工具 / digest
> 写路径）不受影响,继续有效。未来 CLI 为胖客户端形态,其本地内容存储如需 digest 语义,应在客户端侧实现同款
> ref/digest 抽象（语义设计可复用,物理后端为客户端本地文件）,与服务端无关。

## Context（为什么做）

当前 LLM 的完整响应（长文章 / 大段代码）原样存入 `chat_messages.content`，再在后续每一轮被 `HistoryProvider` 全文读回拼进上下文。问题有二：

1. **挤爆 / 挤掉**：`MessageAssembler._trim_history_by_budget`（`context_mgmt/builder/message_assembler.py:169`）从最新往旧累加，单条超预算即 `break` 丢弃其余全部历史。一条超长消息要么吃光 dialogue 预算挤掉所有更早上下文，要么自己被整体丢弃——两种都让对话「失忆」或逼近窗口上限。
2. **无引用化**：`HistoryProvider._rows_to_history_messages`（`context_mgmt/providers/history.py:93`）直接用全文 `r.content` 构造 `Message`，没有摘要 / 骨架 / 占位机制。

目标：抽象一套**统一的会话内容存储语义**——全文存「真相源」不动，上下文里放「引用占位 + digest」，LLM 需要原文时用工具按 ref + range **按需切片回读**（paging 模型）。Web chat（服务端 DB）与 CLI（用户本地 RunStore 文件）共用同一套 ref/digest 语义，只换物理后端。

最终形成三层上下文：**最近全文 → 超长消息逐条 digest（无损可回读）→ 整会话 summary（有损，memory 现有）**。

关键约束：
- 项目**无 Alembic**，`bootstrap_schema`（`infrastructure/database/database.py:66`）用 `create_all`，**只建新表、不给已有表加列**。故 digest 缓存用**新表**，不给 `chat_messages` 加列。
- chat 全文真相源**已在** `chat_messages.content`，CLI 已在 RunStore artifact——**不迁移数据**，抽象层只做「统一 ref + 带 range 的读 + digest」。
- digest 计算复用 memory 的事件驱动写路径（`turn.completed` + TaskQueue），**绝不在热路径同步算 LLM 摘要**。

---

## 设计总览

### ContentRef（统一引用格式）
- chat：`[ref:msg:<message_id>]`，真相源 = `chat_messages.content`
- CLI：`[ref:art:<artifact_id>]`，真相源 = RunStore artifact
- digest 文本内嵌**锚点**（代码：`函数/类名 (L起-L止)`；文章：分段标题），供 LLM 定向回读，避免整段拉回再次爆窗。

### 三类数据
- **真相源全文**：已存在，不动。
- **DigestRecord**：分段结构（prose / code 块），每段带 char/line 范围 + 摘要(prose) 或骨架(code)。缓存在新表 `message_digests`（chat）/ artifact metadata（CLI）。
- **上下文占位**：读时由 DigestPolicy 生成 `[ref:...] + digest 文本` 替换超长消息。

---

## 架构落点（模块）

| 职责 | 落点 |
|---|---|
| ContentStore 协议 + 两后端 | `infrastructure/storage/`（`MessageStore` 已在此）新增 `content_store.py` |
| ContentRef / DigestRecord / Segment 值对象 | `context_mgmt/digest/types.py` |
| 读时引用化 DigestPolicy | `context_mgmt/digest/policy.py`，在 `HistoryProvider` 中调用 |
| 代码骨架（tree-sitter） | `memory/digest/code_skeleton.py` |
| 文章摘要（LLM） | `memory/digest/prose_summarizer.py`（复用 `LLMGateway` + PromptRegistry，参照 `memory/summary/summarizer.py`） |
| digest 缓存读写 | `memory/digest/store.py` + 新 ORM `infrastructure/database/orm/message_digest_orm.py` |
| 异步写路径 hook + task | `memory/digest/hooks.py` + `memory/digest/tasks.py`（镜像 `memory/hooks.py` + `memory/tasks/summarize.py`） |
| chat 回读工具 | `tools/builtin/message/read_message.py`（新） |
| CLI 回读工具加 range | 扩展 `tools/builtin/artifact/get_artifact.py` |

---

## 工作项

### 1. ContentStore 抽象 + 两后端
- 在 `infrastructure/storage/content_store.py` 定义协议：
  ```
  class ContentStore(ABC):
      async def get(self, ref: str, line_range: tuple[int,int] | None = None) -> ContentSlice | None
      async def exists(self, ref: str) -> bool
  ```
  `ContentSlice` 含 `text / total_lines / returned_range / truncated`。
- **DbMessageContentStore**：解析 `msg:<id>`，用 `ChatMessageRepository.get_by_id`（`infrastructure/database/repositories/chat_message_repo.py`）读 `content`，按 line_range 切片。
- **RunStoreContentStore**：解析 `art:<id>`，包 `RunStore.find_artifact`（`infrastructure/run_store.py:252`），从 `payload["content"]` 切片。
- 不动现有 `put`/落库路径：chat 由 `TurnFinalizer` 负责、CLI 由 `RunStorePersistenceLifecycle` 负责，已是真相源写入方。

### 2. DigestRecord 数据模型 + 缓存表
- `context_mgmt/digest/types.py`：
  - `Segment(kind: Literal["prose","code"], start_line, end_line, anchor: str | None, digest_text: str)`
  - `DigestRecord(ref, total_tokens, segments: list[Segment], generated_at, model: str | None)`
- 新 ORM `MessageDigestOrm`（`infrastructure/database/orm/message_digest_orm.py`），用 `BigIntPKMixin`（`orm/mixins.py`）：字段 `message_id`(UNIQUE) / `session_id` / `segments`(JSON) / `total_tokens` / `source_hash`(全文 hash，判 stale) / `model` / `status`(pending/done/failed) / `created_at`/`updated_at`。
  - **必须**在 `infrastructure/database/orm/__init__.py` 集中导入处注册，`create_all` 才会建表。
- CLI 端 digest 直接写入 artifact 的 `metadata`（`Artifact.metadata` 已是开放 dict，见 `run_store_models.py:84`），无需新表。

### 3. Digest 计算
- **代码骨架（tree-sitter，cheap，同步）** `memory/digest/code_skeleton.py`：
  - 新依赖：`tree-sitter` + `tree-sitter-language-pack`（预编译多语言 grammar，免本地编译）。加入 `pyproject.toml` / 依赖清单。
  - 输入：从 markdown fenced code block 提取的 `(language, code)`；输出：top-level class/func 签名 + 行号区间 → `Segment(kind="code", anchor="def login (L42-78)", digest_text=签名行)`。
  - 语言不识别时降级为「首尾若干行 + 行数」启发式。
- **文章摘要（LLM，贵，异步）** `memory/digest/prose_summarizer.py`：
  - 参照 `memory/summary/summarizer.py:46`：`get_registry().render("memory/digest_prose", ...)` + `LLMRequest(task_type="utility", model_profile="fast", cache_enabled=False)`。新增 prompt 模板 `memory/digest_prose`。
- **切分器** `memory/digest/segmenter.py`：把一条 assistant 消息按 ```` ``` ```` 围栏切成 prose / code 块，分派到上面两条。

### 4. 异步写路径（不阻塞响应）
- `memory/digest/hooks.py`：`install_digest_hooks(bus, queue, *, min_tokens, enabled)`，订阅 `turn.completed`（事件已由 `chat/finalizer.py:312` publish）。handler 仅做轻量判定后 `queue.submit("content.digest", session_id=...)`——**镜像** `memory/hooks.py:33` 的「收事件→轻判定→入队」两级解耦，不在 handler 里算 digest。
- `memory/digest/tasks.py`：`run_digest_task(session_id)` 扫描该 session 中 `tokens > min_tokens` 且无 digest / `source_hash` 不匹配的消息，调切分器+骨架+摘要，`MessageDigestStore.upsert`。同时注册 Celery `@shared_task(name="content.digest")`，参照 `memory/tasks/summarize.py:43`。
- 在 `api/lifespan.py` memory hooks 安装处旁边调用 `install_digest_hooks`。
- settings 增配置段 `memory.digest`（`enabled` / `min_tokens` / `per_message_token_cap`），参照 `memory.summarizer` 的配置域写法。

### 5. 读时引用化（核心止血点）
- `context_mgmt/digest/policy.py` 新增 `DigestPolicy`：
  ```
  def apply(messages: list[HistoryMessage], cap: int, meter, lookup) -> list[HistoryMessage]
  ```
  对每条：`meter.count_messages([m]) > cap` 时，用 `lookup(id)` 取缓存 digest → 替换 content 为 `[ref:msg:<id>]\n<拼接 segments 的 digest 文本(含锚点)>`；**缓存未命中**（刚产生、还没异步算完）→ 降级为廉价截断 + 标记 `degraded:["digest_pending"]`，由下一轮异步补上。
- 改 `HistoryProvider`（`context_mgmt/providers/history.py`）：
  - 构造注入 `digest_policy` + `digest_store`（lookup 源）+ 从 settings 读 `per_message_token_cap`。
  - 在 `provide()` 的 `filter` 之后、构造 `ContentChunk` 之前调用 `DigestPolicy.apply`。这就是文档里标注的「阶段 2」落点。
  - 装配处：`context_mgmt` 的工厂（`build_context_manager` / `forge.context.factory.build_context_builder`）注入新依赖。
- `_trim_history_by_budget`（`message_assembler.py:169`）保持「累计预算」职责不变（单条上限已在 provider 处理），二者形成「单条 cap + 累计 budget」双闸。

### 6. 按需回读工具（paging）
- 新工具 `tools/builtin/message/read_message.py`（继承 `tools/base.py:33` `Tool`）：
  - `name="read_message"`，params：`message_id`(必填) / `line_range`(可选 `[start,end]`)；`required_scope` 视 chat 权限设定。
  - `arun`：经 `DbMessageContentStore.get(f"msg:{message_id}", line_range)` 返回切片 + `total_lines` + `truncated`。
  - 注册进 chat profile 的 `tools_allowed`（`config/sys_config.dev.yaml` 的 `agent_profiles.chat`）。注意启动期 7 项强校验（`agents/profiles.py:26`）要求工具已注册。
- 扩展 `get_artifact`（`tools/builtin/artifact/get_artifact.py`）：加可选 `line_range`，落到 `RunStoreContentStore`/`load_artifact` 后切片，让 CLI 也能定向回读而非整体拉回。
- 回读结果以 `role="tool"` 进上下文，天然受 `ToolResultPolicy` 管，用完下一轮被换出——paging 自洽，无需额外清理。

### 7. 配置与可观测
- `chat_messages.context_meta`（`finalizer.py:175`）已落 `degraded`，新增的 `digest_substituted` / `digest_pending` 标记并入，前端可展示「此条已折叠为引用」。
- 新增 span/log：digest 计算耗时、命中率（参照 `observability` 现有埋点风格）。

---

## 不做（避免过度设计）
- 不引入通用 ACL / share 权限模型（ContentStore 只读够用）。
- 不抽象「事件→入队」通用工厂（目前仅 summary + digest 两处，第三个出现再抽）。
- 不改动 CLI 现有 `[artifact:<id>]` 占位逻辑（`persistence_lifecycle.py:150`），仅给 `get_artifact` 加 range。
- 不迁移任何存量全文数据。

---

## 验证

1. **单测**：
   - `code_skeleton`：喂多语言代码块，断言抽出的签名 + 行号区间。
   - `segmenter`：prose/code 混合消息切分正确。
   - `DigestPolicy.apply`：超 cap 替换为 `[ref:msg:...]`、未命中走降级、未超 cap 原样。
   - `DbMessageContentStore.get` 的 line_range 切片边界。
2. **集成**：起服务（MySQL，见 `database.py` bootstrap）→ 确认 `message_digests` 表自动建出。
3. **端到端**（手测，golden path）：
   - 构造一轮让 LLM 输出超长代码的对话 → 等异步 `content.digest` 完成 → 查 `message_digests` 有记录。
   - 发起新一轮 → 抓 `context_meta.degraded` / `layers`，确认该长消息已被 `[ref:msg:...]` 折叠、更早历史不再被挤掉、`total_ratio` 明显下降。
   - 让 LLM 调 `read_message(message_id, line_range)` 回读某函数 → 确认只返回切片。
   - CLI 路径：`get_artifact(artifact_id, line_range)` 返回切片。
4. **回归**：短消息对话不触发 digest（`min_tokens` 以下），响应延迟与改动前一致（热路径只多一次 token 判定 + 缓存读）。
