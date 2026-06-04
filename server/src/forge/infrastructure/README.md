# `forge.infrastructure` — 基础设施

数据库、缓存、队列、事件总线、JSONL、RunStore、文件存储等技术设施。业务模块依赖这里的抽象，不关心具体后端。

## 设计理念

1. **分层多存储**：关系库存元数据、JSONL 存运行态日志、FileStorage 存对象、向量/BM25 存索引——各取所长。
2. **函数式 + 全局单例**：DB 不封装成类，`init_engine` / `get_session_factory` / `session_scope` 函数式管理，符合 Python 风格。
3. **统一会话边界**：非 HTTP 路径（后台任务/服务/钩子）统一用 `session_scope()` 上下文管理器（commit-on-success / rollback-on-error），HTTP 路径用 `get_db` 依赖。两者共享同一套事务语义，避免提交语义在各调用点漂移。
4. **可平滑演进**：进程内 EventBus / 注册表先跑通单机，后接 Redis pub/sub 即可平替多进程。

## 模块速览

```
infrastructure/
├── database/         ← engine + session_factory + session_scope + get_db + repositories + ORM
├── cache/            ← RedisClient (模型配置缓存 / 限流 / 幂等)
├── queue/            ← TaskQueue (LocalTaskQueue 默认 / Celery 可选 / Null 降级)  [见子 README]
├── event_bus/        ← EventBus (进程内总线: turn.completed / 配置变更)          [见子 README]
├── storage/          ← FileStorage(Local/S3) + content_store + data_protocols(存储 ABC)
├── jsonl.py          ← JSONL 原语 (append/iter/tail/iter_after + 原子写)
└── run_store.py      ← RunStore: CLI run 运行态 (runs/<id>/ state.json + events.jsonl + artifacts)
```

## 如何使用

```python
# 非 HTTP 路径: 统一用 session_scope (推荐)
from forge.infrastructure.database.database import session_scope
async with session_scope() as db:
    repo = ChatMessageRepository(db)
    ...

# HTTP 路径: FastAPI 依赖
async def handler(db: AsyncSession = Depends(get_db)): ...

# JSONL 追加日志
from forge.infrastructure.jsonl import ...   # cost.jsonl / audit.jsonl / events.jsonl
```

## 如何扩展

- **新仓储**：在 `database/repositories/` 写 Repository（内部只 flush 不 commit，事务边界交给 `session_scope` / `get_db`）。
- **切队列后端**：实现 `TaskQueue` 接口（Local→Celery）。
- **切文件后端**：实现 `FileStorage`（Local→S3），DB 仍只存 key。
- **新存储契约**：在 `storage/data_protocols.py` 加 ABC（注意：当前多为单实现，按需而非提前抽象）。

## 边界与注意

- 运行态两类均不入关系库：chat turn 事件（`chat_runs/<message_id>/`）、CLI run（`runs/<run_id>/`）。
- Repository 只 `flush` 不 `commit`；提交由会话上下文统一管理。
