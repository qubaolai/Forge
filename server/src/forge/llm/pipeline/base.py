"""Pre/Post 中间件 Protocol + PipelineRunner.

设计:
    - Pre 中间件: 在 LLMDispatcher 之前运行. 可以"短路"(返回 LLMResponse) 表示
      不需要实际调用 LLM (例: 缓存命中 / 幂等去重命中). 也可以抛异常 reject 请求
      (例: 输入校验失败 / 预算超额 / 限流).
    - Post 中间件: 在 LLMDispatcher 成功返回后运行. 主要做记录 / 缓存写入 / 指标
      (不能改写 LLMResponse 的核心 content, 但可附加 cache_hit / cost 等元信息).

OCP:
    新增横切关注点 (限流 / 缓存 / 幂等 / 审计) 只需新增 middleware 并注册到
    PipelineRunner, 不修改 LLMGateway 或 LLMDispatcher 任何代码.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from ..request import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


@runtime_checkable
class PreMiddleware(Protocol):
    """请求侧中间件.

    Returns:
        - None: 继续下一个 Pre middleware / 进入 Dispatcher
        - LLMResponse: 短路返回 (缓存命中 / 幂等命中). 后续 Pre middleware 不再执行
          Post middleware 仍然执行 (例如审计要记录 cache_hit=True 的请求)
        - 抛异常: reject 请求 (例如 BudgetExceeded / InputValidationError)
    """

    async def process(self, req: LLMRequest) -> LLMResponse | None: ...


@runtime_checkable
class PostMiddleware(Protocol):
    """响应侧中间件.

    在实际 LLM 调用成功后执行 (非流式). 流式模式下 Post 在流耗尽后通过 finally 执行.

    Returns:
        LLMResponse: 可以原样返回或附加元信息. 不应改写 content / model 等核心字段.
    """

    async def process(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse: ...


class PipelineRunner:
    """组合 Pre + Post 中间件链, 提供统一执行入口.

    实例不可变 (中间件列表在构造时确定), 可在多个请求间共享. 中间件本身
    需要线程/协程安全 (无内部可变状态或自带锁).
    """

    def __init__(
        self,
        pre_middlewares: list[PreMiddleware] | None = None,
        post_middlewares: list[PostMiddleware] | None = None,
    ) -> None:
        self._pre = list(pre_middlewares or [])
        self._post = list(post_middlewares or [])

    @property
    def pre_count(self) -> int:
        return len(self._pre)

    @property
    def post_count(self) -> int:
        return len(self._post)

    async def run_pre(self, req: LLMRequest) -> LLMResponse | None:
        """依次执行 Pre middleware. 任一返回非 None 即短路."""
        for mw in self._pre:
            try:
                result = await mw.process(req)
            except Exception:
                logger.exception(
                    "Pre middleware %s 抛异常, 终止请求", type(mw).__name__
                )
                raise
            if result is not None:
                logger.debug(
                    "Pre middleware %s 短路返回, 跳过剩余 Pre 与 Dispatcher",
                    type(mw).__name__,
                )
                return result
        return None

    async def run_post(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse:
        """依次执行 Post middleware. 异常打日志但不影响响应返回."""
        for mw in self._post:
            try:
                resp = await mw.process(req, resp)
            except Exception:
                logger.exception(
                    "Post middleware %s 抛异常 (已忽略, 保留响应)", type(mw).__name__
                )
        return resp


__all__ = ["PipelineRunner", "PostMiddleware", "PreMiddleware"]
