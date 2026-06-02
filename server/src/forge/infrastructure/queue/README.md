# `agent_platform.infrastructure.queue` — 异步任务队列

业务侧把 "重活" (LLM 调用 / 批量 IO) 派发到队列的统一抽象.  
当前默认实现是 **LocalTaskQueue + SQLite(`tasks.db`)**，同进程执行但可重试/恢复；  
Celery 作为兼容后端按配置启用 (`ASSISTANT_TASK_QUEUE_BACKEND=celery`)。

## 为什么需要任务队列

记忆摘要的工作链路:

```
chat 完成一轮 → 触发摘要 →
    1. 从 DB 取 100 条历史
    2. 调 LLM 生成摘要 (~3~10 秒)
    3. upsert 到 DB
```

如果在 web 进程里 `await summarize(...)` 同步跑, 用户的下一次请求要等. 即便 fire-and-forget `asyncio.create_task`, 进程崩了任务也丢. 任务队列解决:

| 需求 | EventBus / create_task | LocalTaskQueue | Celery |
|---|---|---|---|
| 失败自动重试 | ❌ | ✅ (`max_attempts`) | ✅ |
| 任务崩溃后恢复 | ❌ | ✅ (`running -> pending`) | ✅ |
| 跨进程横向扩展 | ❌ | ❌ | ✅ |

## 模块速览

```text
queue/
├── base.py             ← TaskQueue ABC (submit only)
├── null.py             ← NullTaskQueue
├── local_store.py      ← tasks.db 持久化状态机
├── local.py            ← LocalTaskQueue (默认)
├── handlers.py         ← 任务名到执行入口映射
├── celery_queue.py     ← Celery 兼容后端
├── celery_app.py       ← Celery worker 入口
└── __init__.py         ← 单例工厂 + 后端选择
```

## 协议契约

```python
class TaskQueue(ABC):
    def submit(self, task_name: str, **kwargs: Any) -> None: ...
```

唯一方法 `submit`, fire-and-forget:

- 业务方不等结果. 真要拿结果, 让 worker 把结果写回 DB, 调用方下次查 DB.
- 任务用**字符串名**寻址 (eg `"memory.summarize"`), worker 端通过 `@shared_task(name="memory.summarize")` 注册.
- `kwargs` 必须 **JSON 可序列化** (str / int / dict / list). 传 ORM 对象 / 自定义类会在 broker 序列化时崩.

### 为什么是字符串名

业务侧调用:

```python
queue.submit("memory.summarize", session_id="sess_abc")
```

而不是:

```python
from agent_platform.memory.tasks.summarize import summarize_task
summarize_task.delay(session_id="sess_abc")    # 业务 import 装饰器
```

字符串名的好处:
- 业务代码**零 Celery 依赖** — 换 Temporal 时, `submit` 实现里改成 `temporal_client.start_workflow(task_name, ...)`, 业务一行不动
- 命名集中: 跟 EventBus 一样, "subsystem.action" 形式, 全项目 grep 一下就能看到所有任务
- 单测友好: fake `TaskQueue` 只看 `submit` 调用记录就行

## 已注册任务

| 任务名 | 实现 | 触发方 | kwargs |
|---|---|---|---|
| `memory.summarize` | `memory.tasks.summarize:run_summarize_task` | `memory.hooks` | `session_id: str` |
| `observability.cost.flush` | `observability.cost.tasks.flush_cost:run_flush_cost_task` | 定时/手动触发 | 无 |

未来:
- `memory.extract_facts` — Stage 3 事实抽取
- `memory.prune_facts` — Stage 3+ 配合 `ForgettingPolicy` 物理删除

## 启动流程

### Web 进程 (FastAPI)

```python
# lifespan.py
init_task_queue(
    enabled=settings.celery.enabled,
    broker_url=settings.celery.broker_url or settings.redis.url,
    backend_url=...,
    task_modules=["agent_platform.memory.tasks"],
)
```

- 默认: `LocalTaskQueue` (无需 Redis/Celery)
- `ASSISTANT_TASK_QUEUE_BACKEND=celery` 时走 Celery 路径
- `enabled=False` 时走 `NullTaskQueue`

### LocalTaskQueue 状态机

`tasks.db.local_tasks` 状态:

`pending -> running -> succeeded`  
`pending -> running -> pending(retry)`  
`pending -> running -> dead`

关键行为:
- 重试: `attempts < max_attempts` 时按 `retry_delay_seconds` 回队
- 死信: 达到上限后 `status=dead`
- 重启恢复: 启动时把残留 `running` 任务恢复成 `pending`

### Worker 进程 (仅 Celery 后端)

```bash
poetry install -E memory
celery -A agent_platform.infrastructure.queue.celery_app worker -l info
```

`celery_app.py` 模块在 import 时:
1. 读 settings
2. 调 `build_celery_app(...)` 装配 app (同样的 broker_url / 同样的 task_modules)
3. 暴露 `app` 模块级符号给 `celery -A` 命令

worker 进程不跑 FastAPI lifespan, 但 `build_celery_app` 注册了 `worker_process_init` 信号, 进程启动时自动 `init_engine()` 初始化 DB.

## 失败与重试

统一约定:
- 业务异常: 任务函数自己吞掉/返回 (不重试)
- 基础设施异常: 允许抛出, 由队列后端重试机制接管

Celery wrapper 示例:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def summarize_task(self, session_id: str):
    try:
        asyncio.run(_run(session_id))
    except _RetryableError as exc:
        raise self.retry(exc=exc) from exc      # 基础设施失败: DB / LLM 网络
    # 业务异常 (LLM 返回空 / 数据为空) -> 不抛, 仅日志, 不重试
```

LocalTaskQueue 会把异常信息写入 `last_error`, 便于排查 `dead` 任务.

## 配置

```yaml
# config/sys_config.yaml
celery:
  enabled: ${CELERY_ENABLED:true}
  broker_url: ${CELERY_BROKER_URL:}
  backend_url: ${CELERY_BACKEND_URL:}
```

部署建议:
- 单机默认不需要 Celery/Redis
- 需要跨进程扩展时再切换 Celery

### Celery 关键设置 (`celery_queue.py` 已写死)

| 设置 | 值 | 理由 |
|---|---|---|
| `task_acks_late` | `True` | worker 崩了任务能被另一 worker 接走 |
| `task_reject_on_worker_lost` | `True` | 同上, 配套 |
| `worker_prefetch_multiplier` | `1` | 摘要任务跑较慢, 不预取, 公平分配 |
| `broker_connection_retry_on_startup` | `True` | Redis 起得比 worker 慢时不立刻挂 |
| `task_serializer / result_serializer` | `json` | 拒绝 pickle (跨语言友好 + 安全) |

## 扩展: 换后端

未来想从 Celery 换成 Temporal:

1. 实现 `infrastructure/queue/temporal_queue.py`:
    ```python
    class TemporalTaskQueue:
        def __init__(self, client):
            self._client = client
        def submit(self, task_name, **kwargs):
            self._client.start_workflow(task_name, kwargs=kwargs)
    ```

2. 改 `__init__.py:init_task_queue` 内部分发逻辑:
    ```python
    if backend == "celery": ...
    elif backend == "temporal": ...
    ```

3. worker 进程改用 Temporal worker 启动方式. 任务函数本身的业务逻辑 (`_run`) 不动.

业务侧 `queue.submit("memory.summarize", ...)` 完全不感知.

## 跟 EventBus 的关系

见 [EventBus README](../event_bus/README.md#跟-taskqueue-的边界). 典型组合: subscriber 收到事件 → 决策 → submit 重活.

## 相关文档

- 事件总线: [`infrastructure/event_bus`](../event_bus/)
- 当前唯一任务: [`memory.tasks.summarize`](../../memory/tasks/summarize.py)
- 触发点: [`memory.hooks`](../../memory/hooks.py)
