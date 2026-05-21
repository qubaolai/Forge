"""DashScope (千问) 文本 reranker 实现.

调用 SDK: dashscope.TextReRank.call(...)
推荐模型: gte-rerank / gte-rerank-v2

错误处理:
    - 鉴权 / 参数错误 (4xx): 直接抛 RerankError, 重试无意义
    - 网络超时 / 连接错误 / 5xx: 指数退避重试, 用尽后抛 RerankError
    - SDK 不抛异常时, 通过 status_code 判定成功 (200) 或失败

文档截断:
    - 委托给 TruncationStrategy, 默认 "tail" 尾截
    - 策略和参数走 config['truncation']
"""

from __future__ import annotations

import logging
import time
from http import HTTPStatus

from forge.llm.cost_tracker import get_cost_tracker

from .base import Reranker, RerankError, RerankResult
from .factory import register_reranker
from .truncation import TruncationFactory, TruncationStrategy

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "dashscope"


# 4xx 状态码视为 "客户端错误", 不重试
# TODO: 不可重试错误码当前按 HTTP status 粗筛, 实际 DashScope 的 code 字段
#       (如 "Throttling.RateQuota") 携带更细粒度信息, 后续可按 code 精细化
_NON_RETRYABLE_HTTP = {
    HTTPStatus.BAD_REQUEST,
    HTTPStatus.UNAUTHORIZED,
    HTTPStatus.FORBIDDEN,
    HTTPStatus.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED,
    HTTPStatus.UNPROCESSABLE_ENTITY,
}


@register_reranker("dashscope")
class DashScopeReranker(Reranker):
    """DashScope gte-rerank 实现.

    Config:
        api_key:        必填
        model:          默认 "gte-rerank"
        timeout:        单次调用超时 (秒), 默认 5.0
        max_retries:    最大重试次数 (不含首次), 默认 2
        retry_backoff:  退避基准 (秒), 默认 1.0
        truncation:     dict, 截断策略配置
            strategy:           策略名, 默认 "tail"
            max_doc_chars:      单文档最大字符数, 默认 4000
            monitor_threshold:  批次截断率告警阈值 (tail 策略用), 默认 0.1
    """

    def __init__(self, config: dict):
        super().__init__(config)

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError("DashScopeReranker 需要 config['api_key']")

        # 延迟 import: 没装 dashscope 的环境 (mock / bge_local) 也能 import 本模块
        import dashscope

        self._dashscope = dashscope
        self._api_key = api_key
        self._model = config.get("model", "gte-rerank")
        self._timeout = float(config.get("timeout", 5.0))
        self._max_retries = int(config.get("max_retries", 2))
        self._retry_backoff = float(config.get("retry_backoff", 1.0))

        # 截断策略 (默认 tail)
        trunc_cfg = dict(config.get("truncation") or {})
        strategy_name = trunc_cfg.pop("strategy", "tail")
        self._max_doc_chars = int(trunc_cfg.pop("max_doc_chars", 4000))
        self._truncation: TruncationStrategy = TruncationFactory.create(
            strategy_name,
            trunc_cfg,
        )

        logger.info(
            "DashScopeReranker 就绪: model=%s timeout=%.1fs max_retries=%d "
            "truncation=%s max_doc_chars=%d",
            self._model,
            self._timeout,
            self._max_retries,
            self._truncation.name,
            self._max_doc_chars,
        )

    @property
    def model_name(self) -> str:
        return self._model

    # ==================================================================
    # 主入口
    # ==================================================================
    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[RerankResult]:
        if not query:
            raise ValueError("query 不能为空")
        if not documents:
            return []

        # 查询路径: 走 user 预算检查. 超额抛 LLMBudgetExceeded, 由
        # ParentChildRetriever / knowledge_search 工具决定如何回灌给用户.
        get_cost_tracker().check_budget()

        # 本地夹一下 top_n, 避免无谓的 API 拒绝
        n = len(documents)
        effective_top_n = n if top_n is None else max(1, min(top_n, n))

        truncated_docs = self._truncation.truncate(
            query,
            documents,
            self._max_doc_chars,
        )

        resp = self._call_with_retry(query, truncated_docs, effective_top_n)
        self._record_cost(resp)
        return self._parse_response(resp, n)

    def _record_cost(self, resp) -> None:
        """从 rerank 响应里抓 usage.total_tokens 上报 CostTracker (容错)."""
        try:
            usage = getattr(resp, "usage", None) or {}
            if hasattr(usage, "get"):
                total = usage.get("total_tokens") or usage.get("input_tokens") or 0
            else:
                total = getattr(usage, "total_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
            if not total:
                return
            get_cost_tracker().record(
                provider=_PROVIDER_NAME,
                model=self._model,
                usage={"prompt_tokens": int(total), "completion_tokens": 0},
            )
        except Exception:  # noqa: BLE001
            logger.debug("DashScope reranker 计费上报失败 (已忽略)", exc_info=True)

    # ==================================================================
    # 带重试的调用
    # ==================================================================
    def _call_with_retry(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ):
        last_err: Exception | None = None
        attempts = self._max_retries + 1  # 首次 + 重试次数

        for attempt in range(1, attempts + 1):
            try:
                resp = self._dashscope.TextReRank.call(
                    api_key=self._api_key,
                    model=self._model,
                    query=query,
                    documents=documents,
                    top_n=top_n,
                    return_documents=False,
                    timeout=self._timeout,
                )
            except Exception as e:
                # SDK 抛出的网络/连接异常视为可重试
                last_err = e
                logger.warning(
                    "DashScope rerank 第 %d/%d 次调用抛异常: %s",
                    attempt,
                    attempts,
                    e,
                )
                if attempt < attempts:
                    self._sleep_backoff(attempt)
                continue

            status = getattr(resp, "status_code", None)
            if status == HTTPStatus.OK:
                return resp

            code = getattr(resp, "code", "") or ""
            message = getattr(resp, "message", "") or ""
            request_id = getattr(resp, "request_id", "") or ""

            if status in _NON_RETRYABLE_HTTP:
                raise RerankError(
                    f"DashScope rerank 客户端错误 (不可重试): "
                    f"status={status} code={code} message={message} "
                    f"request_id={request_id}"
                )

            last_err = RerankError(
                f"DashScope rerank 失败: status={status} code={code} "
                f"message={message} request_id={request_id}"
            )
            logger.warning(
                "DashScope rerank 第 %d/%d 次返回非 200: status=%s code=%s message=%s",
                attempt,
                attempts,
                status,
                code,
                message,
            )
            if attempt < attempts:
                self._sleep_backoff(attempt)

        raise RerankError(
            f"DashScope rerank 重试 {self._max_retries} 次后仍失败: {last_err}"
        ) from last_err

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._retry_backoff * (2 ** (attempt - 1))
        time.sleep(delay)

    # ==================================================================
    # 响应解析
    # ==================================================================
    @staticmethod
    def _parse_response(resp, doc_count: int) -> list[RerankResult]:
        try:
            output = resp.output
            results = output["results"] if isinstance(output, dict) else output.results
        except (AttributeError, KeyError, TypeError) as e:
            raise RerankError(f"DashScope 响应结构异常: {e}") from e

        out: list[RerankResult] = []
        for item in results:
            if isinstance(item, dict):
                idx = item.get("index")
                score = item.get("relevance_score")
            else:
                idx = getattr(item, "index", None)
                score = getattr(item, "relevance_score", None)

            if idx is None or score is None:
                raise RerankError(f"DashScope 结果项缺少 index/score: {item!r}")
            if not (0 <= idx < doc_count):
                raise RerankError(f"DashScope 返回 index 越界: {idx} (文档数 {doc_count})")
            out.append(RerankResult(index=int(idx), score=float(score)))
        return out
