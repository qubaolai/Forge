"""TaskQueue 协议: 业务侧把异步任务派发出去, 不感知底层 (Celery / Temporal / RQ).

设计原则:
    - 只有 submit, fire-and-forget. 业务不关心任务执行结果 (摘要任务: 跑成功就
      upsert DB, 跑失败靠 worker 端重试).
    - 任务以 "字符串名" 寻址, 不 import worker 装饰器. 切换 worker 实现时业务
      代码零改动.
    - submit 的 kwargs 必须是 JSON 可序列化的 plain 类型 (字符串/数字/dict/list).
      传 ORM 对象 / 自定义类 会在 broker 序列化时崩.
"""

from __future__ import annotations

from typing import Any, Protocol


class TaskQueue(Protocol):
    """异步任务派发. 长寿单例, 并发安全."""

    def submit(self, task_name: str, **kwargs: Any) -> None:
        """把任务交给后端 worker. 不等结果, 不抛业务异常.

        Args:
            task_name: 在 worker 端注册过的任务名 (eg "memory.summarize").
            **kwargs: 任务参数, 必须 JSON 可序列化.

        Raises:
            仅在 broker 完全不可达 / 配置错误时抛 (启动期错误的延伸).
            业务调用方应假定 submit 总是成功; 真正的任务失败由 worker 重试.
        """
        ...
