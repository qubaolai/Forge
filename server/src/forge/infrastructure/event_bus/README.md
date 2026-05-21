# `agent_platform.infrastructure.event_bus` — 事件总线

进程内 / 跨进程 pub/sub 抽象. 业务模块之间不通过直接调用耦合, 而是通过事件名解耦.

## 为什么需要事件总线

典型场景: chat 路由处理完一轮对话后, 需要触发若干 "副作用":

- 派发摘要任务
- 派发事实抽取任务 (Stage 3+)
- 更新会话标题统计
- 触发审计日志
- ...

如果在 `_stream_chat` 里**直接 import** memory / 审计 / 统计 等模块去调它们的函数:

- chat 路由变成 "所有相关业务的客户端", 改动频繁, 易出 bug
- 单测 chat 路由要 mock 一长串依赖
- 加新业务 (例: 之后想接消息推送) 又要改 chat 一次

事件总线把 "通知发生了什么事" 与 "谁关心" 分开:

```
chat: publish("turn.completed", {...})           ← 只关心 "发生了"
memory.hooks: subscribe("turn.completed", ...)   ← 只关心 "听到了"
audit.hooks:  subscribe("turn.completed", ...)   ← 互不感知
```

## 模块速览

```
event_bus/
├── base.py          ← EventBus Protocol + EventHandler / EventPayload 类型
├── in_process.py    ← InProcessEventBus: asyncio.create_task fire-and-forget
├── kafka.py         ← (扩展点, placeholder) Kafka 实现
├── redis_pubsub.py  ← (扩展点, placeholder) Redis Pub/Sub 实现
└── __init__.py      ← get_event_bus / reset_event_bus 单例工厂
```

## 协议契约

```python
class EventBus(Protocol):
    def subscribe(self, event_name: str, handler: EventHandler) -> None: ...
    async def publish(self, event_name: str, payload: EventPayload) -> None: ...

EventPayload = dict[str, Any]
EventHandler = Callable[[EventPayload], Awaitable[None]]
```

### 三条不可妥协的约定

1. **`publish` 不阻塞**: handler 用 `asyncio.create_task` 调度, publisher 立即返回. chat 路由发完事件就走, 不等下游处理.
2. **`publish` 不抛**: handler 抛出的异常被 EventBus 吞掉 + 记 `logger.exception`. 一个出错的 subscriber 不能反噬 publisher 或其他 subscriber.
3. **payload 是 plain dict**: 跨进程实现 (Kafka / Redis Pub/Sub) 要序列化时, 不被自定义类型卡住.

## 当前实现: `InProcessEventBus`

进程内 pub/sub, 不持久化. 单实例, asyncio 单线程环境下并发安全.

```python
class InProcessEventBus:
    def subscribe(self, event_name, handler):
        self._subscribers.setdefault(event_name, []).append(handler)

    async def publish(self, event_name, payload):
        for h in self._subscribers.get(event_name, []):
            asyncio.create_task(_safe_call(event_name, h, payload))
```

**适用场景**: 同一 Python 进程内的 publisher / subscriber. 不适合:
- web 进程 publish, Celery worker 进程 subscribe → 跨进程, 走 Redis Pub/Sub / Kafka
- 高吞吐持久化要求 → 走 Kafka

## 已知事件名

| 事件名 | publisher | 当前 subscriber | payload |
|---|---|---|---|
| `turn.completed` | `api.routes.v1.chat:_stream_chat` (正常完成) | `memory.hooks.on_turn_completed` | `{session_id, user_id, trace_id}` |

未来事件 (规划中):
- `turn.error` — 用于审计 / 监控
- `session.created` / `session.deleted` — 给 memory 清理 summary / facts
- `fact.added.manual` — 用户手动加事实时 (前端调 API)

## 命名规约

`<subsystem>.<event>` 格式, 小写, 点分:

- ✅ `turn.completed` / `session.deleted` / `fact.added.manual`
- ❌ `TurnCompleted` / `turn_completed` / `complete-turn`

**事件名是契约**, 改名相当于 break 所有 subscriber. 真要换语义建议**先加新事件名, 双发, 灰度迁移后再删旧名**.

## 使用示例

### 注册 subscriber (启动期)

```python
# 在 lifespan 启动时
from agent_platform.infrastructure.event_bus import get_event_bus

def install_hooks():
    bus = get_event_bus()

    async def on_turn(payload):
        session_id = payload["session_id"]
        # 业务处理

    bus.subscribe("turn.completed", on_turn)
```

### 发布事件 (业务路径)

```python
from agent_platform.infrastructure.event_bus import get_event_bus

# fire-and-forget
await get_event_bus().publish("turn.completed", {
    "session_id": session_id,
    "user_id": user_id,
    "trace_id": trace_id,
})
```

注意 `publish` 是 `async def` 但**不等 handler 完成**. 它只负责把 handler 调度到事件循环, 立即返回. 想要可靠的 "等下游处理完" 语义, 别用 EventBus, 用 `TaskQueue` (带重试 + 持久化的 broker).

## 扩展: 跨进程总线

未来需要 web 进程 publish, worker 进程 subscribe (例: 工具执行进度推送) 时, 写一个 `RedisPubSubEventBus`:

```python
class RedisPubSubEventBus:
    def __init__(self, redis_client):
        self._redis = redis_client
        self._subscribers: dict[str, list[EventHandler]] = {}
        # 启动后台任务, 不断 redis.pubsub().get_message(), 分发

    def subscribe(self, event_name, handler):
        # 给每个 event_name 第一次订阅时, 在 redis 上 subscribe 对应 channel
        self._subscribers.setdefault(event_name, []).append(handler)
        if len(self._subscribers[event_name]) == 1:
            self._pubsub.subscribe(f"events:{event_name}")

    async def publish(self, event_name, payload):
        await self._redis.publish(f"events:{event_name}", json.dumps(payload))
```

把 `get_event_bus()` 内部按 settings 分发: `InProcessEventBus` (默认) / `RedisPubSubEventBus` (跨进程场景), 调用方代码不动.

## 跟 TaskQueue 的边界

| | EventBus | TaskQueue |
|---|---|---|
| 语义 | "通知发生了什么" | "派发后台干活" |
| 持久化 | ❌ 进程级 | ✅ broker 落盘 |
| 失败重试 | ❌ 一次性 | ✅ Celery retry |
| 适用 | 多 subscriber 各做轻量逻辑 | 重活 (LLM 调用 / 大量 IO) |
| 调用代价 | μs 级 (asyncio.create_task) | ms 级 (broker 序列化 + 网络) |

**典型组合**: subscriber 收到事件后, 做条件判断 (eg 第 10 轮?), 决定要不要 `queue.submit` 重活. 见 [`memory/hooks.py`](../../memory/hooks.py).

## 相关文档

- 任务队列: [`infrastructure/queue`](../queue/)
- 主要消费方: [`memory`](../../memory/) (turn.completed 订阅)
