# `agent_platform.infrastructure.storage` — 存储抽象层

本模块包含 **两类** 不互相干扰的抽象:

| 类别 | 入口 | 用途 |
|---|---|---|
| **Blob 文件存储** | `base.py:FileStorage` / `local_fs.py:LocalFileStorage` | KB 上传文件等大对象存储, 本地磁盘 / S3 等 |
| **结构化数据存储** | `data_protocols.py` (Protocols) + `data_factory.py` (装配) | 会话 / 消息 / 摘要 / 成本 / 审计 / KB 元数据 |

两类分开是因为接口契约完全不同(blob 是 bytes in/out,structured 是结构化对象 CRUD)。

---

## 1. 结构化数据存储 7 个 Protocol (S6.5 M3 落地)

| Protocol | 当前本地实现 | 后端 | 主要使用方 |
|---|---|---|---|
| `SessionStore` | `infrastructure/database/repositories/session_repo.py:SessionRepository` | JSONL `SessionLog` | `chat.preparer` / `chat.resumer` / `api/routes/v1/chat.py` |
| `MessageStore` | `infrastructure/database/repositories/message_repo.py:MessageRepository` | JSONL `SessionLog` (message 段) | `chat.preparer / assembler / finalizer / orchestrator / resumer`, `memory.hooks`, `memory.summary.service`, `context.builder` |
| `CostStore` | `infrastructure/cost_log.py:CostLog` | `cost.jsonl` | `llm.cost_tracker` 等 |
| `AuditStore` | `infrastructure/audit_log.py:AuditLog` | `audit.jsonl` | `guardrails.compliance.audit_logger` |
| `SummaryStore` | `memory/summary/store.py:SummaryStore` | MySQL `session_summaries` | `memory.composite`, `memory.summary.service` |
| `KnowledgeBaseStore` | `infrastructure/database/repositories/knowledge_base_repo.py:KnowledgeBaseRepository` | SQLite `kb.db` | `api/services/kb_service`, `api/services/kb_ingest_service`, `chat.assembler` |
| `KbDocumentStore` | `infrastructure/database/repositories/kb_document_repo.py:KbDocumentRepository` | SQLite `kb.db` | `api/services/kb_service`, `api/services/kb_ingest_service` |

**不在本期 Protocol 内**: `AgentRepository` (5-8 业务文件用得少, 暂保留直接 import) 与 `KbDocumentChunkRepository` (chunk 写入路径专属, 不属于 7 Protocol 边界).

---

## 2. 业务代码怎么用

```python
# ❌ 旧写法 (S6.5 M3 前)
from agent_platform.infrastructure.database.repositories.message_repo import MessageRepository
repo = MessageRepository(db)

# ✅ 新写法 (S6.5 M3 起)
from agent_platform.infrastructure.storage import make_message_store, MessageStore
repo: MessageStore = make_message_store(db)
```

两处差异:
1. **import**: 走 `infrastructure.storage` 统一入口, 不直接碰具体 Repository 类.
2. **实例化**: `make_*_store()` 工厂函数检查 `deployment_mode`, local 模式下返回 ``Repository`` 实例(行为零变化);非 local 模式抛 `NotImplementedError`.

类型注解用 Protocol(`MessageStore` / `SessionStore` / ...),代码读起来更明确"这里是个 store 抽象,不是具体实现"。

---

## 3. 部署模式

```python
from agent_platform.infrastructure.storage import get_deployment_mode

mode = get_deployment_mode()  # "local" | "sandbox" | "cloud" | ...
```

读取顺序(S6.5 M4 起):
1. 测试/脚本临时覆盖:环境变量 `DEPLOYMENT_MODE` (大小写 / 前后空格忽略).
2. 正常应用配置:`settings.deployment_mode`,来自 `config/sys_config*.yaml`.
3. 兜底 `local`.

| 模式 | 行为 |
|---|---|
| `local` (默认) | 所有 `make_*_store` 返回本地实现 |
| `sandbox` | 抛 `NotImplementedError("S6.5 阶段未实现")` |
| `cloud` | 同上 |

---

## 4. 加新 backend 的 4 步流程

举例: 接入 RDS 作为 SessionStore / MessageStore 的远端 backend.

1. **实现类**:在 `infrastructure/storage/rds/` 下新建 `RdsSessionStore` / `RdsMessageStore`,匹配对应 Protocol 的方法签名。
2. **注册到 factory**:在 `data_factory.py:make_session_store` 等函数内,按 `get_deployment_mode()` 分支:
   ```python
   if mode == "local":  return SessionRepository(db)
   if mode == "cloud":  return RdsSessionStore(db)
   ```
3. **加 Protocol 兼容性测试**:新建 `tests/unit/infrastructure/test_rds_*.py`,断言 `isinstance(impl, SessionStore)` 等。
4. **环境切换**:`DEPLOYMENT_MODE=cloud poetry run uvicorn ...`(或后期 `settings.deployment_mode = "cloud"`)。

业务代码 **一行不改**,这是 Protocol 抽象的核心收益。

---

## 5. 设计边界

- **Protocol 是结构化匹配, 不是名义继承**: 现有 `MessageRepository` 等具体类没显式 `class MessageRepository(MessageStore):`,但方法签名匹配即满足 `isinstance` 检查(`@runtime_checkable` 已加)。

- **Protocol 只列业务用到的方法**: 不是全量 CRUD; 加方法时既要改 Protocol 也要改实现。

- **DB 会话不在 Protocol 上**: `make_message_store(db)` 接收 session 后返回 Protocol 实例; session 生命周期管理仍归调用方(chat 路由的 SSE 生成器 / lifespan)。

- **本期不做 RDS / S3 / Dynamo backend 实现**: 这是真正接 SaaS 时的工作量(M3 只钉接入点)。
