"""LLM 调用成本追踪 + 预算控制 (per-user 维度) + CostLog 持久化.

设计:
    - 进程内累计 (按 user_id × provider × model 三维) 作为"delta since last flush".
    - flush_to_db() 把 delta 追加到 cost.jsonl, 然后清零内存
      并刷新 db_baseline (该进程对当日累计的本地缓存).
    - check_budget 使用 (db_baseline + 内存 delta) 估算当日总额, 决策是否抛 LLMBudgetExceeded.

多进程一致性:
    - 各进程独立持有 CostTracker + db_baseline; 一处写, 通过 flush 进 cost.jsonl,
      下一次 flush / hydrate 时各进程会回读 baseline.
    - 因此短时间窗口内 (≤ flush 周期) 不同进程间预算判断可能略偏低估,
      达不到强一致, 但够用; 强一致需要共享计数器 (P2).

user_id:
    - 优先级: 显式参数 > ContextVar > "" (匿名).
    - "" 用作后台任务 / 系统流量 placeholder, 不走 user 级 budget.

预算控制:
    - BudgetConfig 支持全局上限 + 默认 per-user 上限 + per-user override 上限.
    - check_budget(user_id) 在调用 LLM 前由 fallback chain 调一次:
        * 超出 → 抛 LLMBudgetExceeded (retry.py 不会重试它)
        * 接近 alert_threshold → WARNING 日志, 但放行
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC

from forge.core.request_context import current_user_id

logger = logging.getLogger(__name__)


# 单价表: USD per 1M tokens, (input_price, output_price).
# 关键词匹配 (不写死全名), 大小写不敏感.
# 数据来源: 各家官网公开定价 (2025), 仅供参考, 真实计费以发票为准.
#
# 对于 embedding / reranker:
#   - 只有 input 计费, output_price 恒为 0
#   - record() 时把消耗 token 数填到 usage.prompt_tokens, completion_tokens 填 0
_PRICE_TABLE_USD: dict[str, tuple[float, float]] = {
    # ---- Chat LLM ----
    # OpenAI
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-4o": (2.50, 10.00),
    "o1-mini": (3.00, 12.00),
    "o1-preview": (15.00, 60.00),
    # Anthropic
    "claude-haiku-4": (1.00, 5.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # DeepSeek
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    # DashScope / Qwen (估算)
    "qwen-turbo": (0.05, 0.20),
    "qwen-plus": (0.30, 0.90),
    "qwen-max": (2.40, 9.60),
    "qwen3-max": (2.40, 9.60),
    # ---- Embedding (input-only) ----
    # DashScope text-embedding-v3 ≈ ¥0.0007/1k tokens ≈ $0.10/1M
    "text-embedding-v3": (0.10, 0.0),
    "text-embedding-v2": (0.10, 0.0),
    "text-embedding-v1": (0.10, 0.0),
    # OpenAI
    "text-embedding-3-small": (0.020, 0.0),
    "text-embedding-3-large": (0.130, 0.0),
    "text-embedding-ada-002": (0.10, 0.0),
    # BGE 系列 (本地推理无 API 费, 估算 0)
    "bge-": (0.0, 0.0),
    # ---- Reranker (input-only, 按 query+docs 总 token) ----
    # DashScope gte-rerank ≈ ¥0.004/1k tokens ≈ $0.60/1M
    "gte-rerank": (0.60, 0.0),
}


# user_id="" 用作"无用户上下文"的占位 (后台任务 / 系统流量).
_ANONYMOUS_USER = ""


class LLMBudgetExceeded(Exception):
    """超出 LLM 调用预算时抛出.

    retry.py 的 is_retryable() 仅按关键词匹配 (timeout / 429 / 5xx),
    本异常不在关键词列表里, 自然 NON-retryable. Fallback chain 应直接
    向上抛, 不切换 provider (换 provider 不解决预算问题).
    """


@dataclass(frozen=True)
class BudgetConfig:
    """LLM 预算配置.

    优先级 (高 → 低):
        1. user_daily_limits_usd[user_id]   — 该用户显式上限
        2. default_user_daily_limit_usd     — 所有用户的默认上限
        3. global_daily_limit_usd           — 全局所有调用的总上限
    任何一层超出都触发 LLMBudgetExceeded.

    None 表示不启用该级别限制. 默认全 None 即不做任何限制 (向后兼容).
    """

    user_daily_limits_usd: dict[str, float] = field(default_factory=dict)
    default_user_daily_limit_usd: float | None = None
    global_daily_limit_usd: float | None = None
    alert_threshold: float = 0.8
    """达到该比例时打 WARNING 日志 (不阻断)."""

    def limit_for(self, user_id: str) -> float | None:
        """返回 user 适用的最严格 daily 上限 (None = 该用户不限)."""
        explicit = self.user_daily_limits_usd.get(user_id)
        if explicit is not None:
            return explicit
        return self.default_user_daily_limit_usd


@dataclass
class UsageStat:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    errors: int = 0


# Daily total provider: 返回某 user 当日累计 USD. 在 Phase 4b 注入 DB-backed 实现;
# 不注入时回落到进程内统计 (= 进程启动以来累计, 不严格的 daily).
DailyTotalProvider = Callable[[str], float]


@dataclass
class CostTracker:
    """全局 LLM 用量统计. 三维 key: (user_id, provider, model)."""

    _stats: dict[tuple[str, str, str], UsageStat] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _budget: BudgetConfig = field(default_factory=BudgetConfig)
    _daily_total_provider: DailyTotalProvider | None = field(default=None, repr=False)
    """注入式 daily total 提供方. None 时使用 (db_baseline + 内存 delta)."""

    _db_baseline: dict[str, float] = field(default_factory=dict)
    """该进程读到的 DB 当日累计 (per user_id), 用于 check_budget."""

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def record(
        self,
        provider: str,
        model: str,
        usage: dict | None,
        *,
        error: bool = False,
        user_id: str | None = None,
        quota_controlled: bool = False,
    ) -> None:
        """记录一次 LLM 调用.

        user_id 优先级: 显式参数 > ContextVar > 匿名 ("").
        """
        uid = user_id if user_id is not None else (current_user_id() or _ANONYMOUS_USER)
        key = (uid, provider, model)
        with self._lock:
            stat = self._stats.setdefault(key, UsageStat())
            stat.calls += 1
            if error:
                stat.errors += 1
                return
            prompt = self._extract(usage, ("prompt_tokens", "input_tokens", "prompt_token_count"))
            completion = self._extract(
                usage, ("completion_tokens", "output_tokens", "candidates_token_count")
            )
            stat.prompt_tokens += prompt
            stat.completion_tokens += completion
            estimated_cost = self._estimate_cost(model, prompt, completion)
            stat.estimated_cost_usd += estimated_cost
            if quota_controlled:
                self._record_usage_quota_delta(uid, estimated_cost, prompt + completion)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def configure_budget(self, budget: BudgetConfig) -> None:
        """整体替换预算配置. 通常从 settings 加载后调一次."""
        with self._lock:
            self._budget = budget

    def set_daily_total_provider(self, provider: DailyTotalProvider | None) -> None:
        """注入 daily total 来源 (Phase 4b 用 DB 查询)."""
        self._daily_total_provider = provider

    @property
    def budget(self) -> BudgetConfig:
        return self._budget

    # ------------------------------------------------------------------
    # 预算检查
    # ------------------------------------------------------------------
    def check_budget(
        self,
        user_id: str | None = None,
        *,
        quota_controlled: bool = False,
    ) -> None:
        """在调用 LLM 前检查预算. 超出抛 LLMBudgetExceeded.

        user_id 优先级同 record(): 参数 > ContextVar > 匿名.

        检查顺序:
            1. global_daily_limit_usd (任何 user 共用一个 pool, 包含匿名)
            2. user_daily_limits_usd[user_id] or default_user_daily_limit_usd

        接近 alert_threshold 时打 WARNING 日志 (不抛).
        """
        uid = user_id if user_id is not None else (current_user_id() or _ANONYMOUS_USER)
        budget = self._budget

        # 全局上限
        if budget.global_daily_limit_usd is not None:
            total = self._global_total()
            self._maybe_alert(
                "全局", None, total, budget.global_daily_limit_usd, budget.alert_threshold
            )
            if total >= budget.global_daily_limit_usd:
                _inc_budget_exceeded_metric(uid)
                raise LLMBudgetExceeded(
                    f"全局 daily 预算已用尽: ${total:.4f} / ${budget.global_daily_limit_usd:.4f}"
                )

        # User 级上限 (匿名用户不查 user 级限制, 走全局)
        user_limit = budget.limit_for(uid)
        if uid and user_limit is not None:
            total = self._user_total(uid)
            self._maybe_alert(f"用户 {uid}", uid, total, user_limit, budget.alert_threshold)
            if total >= user_limit:
                _inc_budget_exceeded_metric(uid)
                raise LLMBudgetExceeded(
                    f"用户 {uid} daily 预算已用尽: ${total:.4f} / ${user_limit:.4f}"
                )

        if quota_controlled:
            self._check_usage_quota(uid)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, dict]:
        """返回当前累计统计的快照, 可用于 /metrics 或日志.

        key 格式: "user_id:provider:model". 匿名用户 user_id 为 "anon".
        """
        with self._lock:
            return {
                self._key_str(k): {
                    "user_id": k[0] or "anon",
                    "provider": k[1],
                    "model": k[2],
                    "calls": v.calls,
                    "prompt_tokens": v.prompt_tokens,
                    "completion_tokens": v.completion_tokens,
                    "estimated_cost_usd": round(v.estimated_cost_usd, 6),
                    "errors": v.errors,
                }
                for k, v in self._stats.items()
            }

    def user_total_in_memory(self, user_id: str) -> float:
        """该 user 在进程内累计的 USD (不含 DB)."""
        return self._sum_filtered(lambda k: k[0] == user_id)

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()
            self._db_baseline.clear()

    # ------------------------------------------------------------------
    # 持久化 (异步, 由 Celery beat 任务 / lifespan 调用)
    # ------------------------------------------------------------------
    async def flush_to_db(self) -> int:
        """把内存 delta 追加到 cost.jsonl, 清零内存, 刷新 db_baseline.

        Returns:
            写入条目数 (不含 0 调用条目).
        """
        # 1. 原子地把当前 stats 取出 + 清零, 避免 flush 期间 record() 丢失
        from datetime import datetime

        snapshot_cutoff = datetime.now(UTC)
        with self._lock:
            snapshot = dict(self._stats)
            self._stats.clear()

        if not snapshot:
            return 0

        from forge.infrastructure.cost_log import CostEntry, default_cost_log

        today = datetime.now(UTC).date()
        written = 0
        cost_log = default_cost_log()
        now = datetime.now(UTC)
        for (user_id, provider, model), stat in snapshot.items():
            if stat.calls == 0:
                continue
            await cost_log.record(
                CostEntry(
                    provider=provider,
                    model=model,
                    prompt_tokens=stat.prompt_tokens,
                    completion_tokens=stat.completion_tokens,
                    cost_usd=stat.estimated_cost_usd,
                    error=(stat.errors > 0 and stat.calls == stat.errors),
                    user_id=user_id or None,
                    call_count=stat.calls,
                    error_count=stat.errors,
                    ts=now,
                )
            )
            written += 1

        # flush 后顺手刷新 baseline (今天 per-user 总和).
        new_baseline = await self._baseline_from_cost_log(today)

        with self._lock:
            self._db_baseline = new_baseline
        try:
            from forge.quota import get_usage_quota_manager

            quota = get_usage_quota_manager()
            await quota.hydrate_baseline()
            quota.mark_persisted(snapshot_cutoff)
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度 baseline 刷新失败, 将继续按内存 delta 判断")
        logger.info(
            "LLM 成本 flush 完成 entries=%d baseline_users=%d",
            written,
            len(new_baseline),
        )
        return written

    async def hydrate_baseline(self) -> None:
        """启动期从 cost.jsonl 装载当日累计到 db_baseline.

        web / worker 进程在启动后调一次, 确保第一次 check_budget 能看到真值.
        """
        from datetime import datetime

        today = datetime.now(UTC).date()
        baseline = await self._baseline_from_cost_log(today)

        with self._lock:
            self._db_baseline = baseline
        try:
            from forge.quota import get_usage_quota_manager

            await get_usage_quota_manager().hydrate_baseline()
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度 baseline 装载失败, 将继续按内存 delta 判断")
        logger.info("LLM 成本 baseline 装载完成 users=%d", len(baseline))

    async def _baseline_from_cost_log(self, day) -> dict[str, float]:
        from datetime import datetime, time

        from forge.infrastructure.cost_log import default_cost_log

        start = datetime.combine(day, time.min, tzinfo=UTC)
        grouped = await default_cost_log().aggregate("user_id", since=start)
        baseline: dict[str, float] = {}
        for user_key, total in grouped.items():
            uid = "" if user_key == "_" else user_key
            baseline[uid] = baseline.get(uid, 0.0) + float(total or 0.0)
        return baseline

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _user_total(self, user_id: str) -> float:
        """优先用注入的 daily provider, 否则使用 (db_baseline + 内存 delta)."""
        if self._daily_total_provider is not None:
            try:
                return float(self._daily_total_provider(user_id))
            except Exception:  # noqa: BLE001
                logger.exception("daily_total_provider 失败, 回退到 baseline+内存")
        with self._lock:
            baseline = self._db_baseline.get(user_id, 0.0)
        return baseline + self.user_total_in_memory(user_id)

    def _global_total(self) -> float:
        """全局累计 USD (含所有 user). 只用进程内统计 (DB 全局太重)."""
        return self._sum_filtered(lambda _k: True)

    def _sum_filtered(self, predicate: Callable[[tuple[str, str, str]], bool]) -> float:
        with self._lock:
            return sum(v.estimated_cost_usd for k, v in self._stats.items() if predicate(k))

    @staticmethod
    def _record_usage_quota_delta(user_id: str, cost_usd: float, tokens: int) -> None:
        try:
            from forge.quota import get_usage_quota_manager

            get_usage_quota_manager().record(
                user_id=user_id,
                cost_usd=cost_usd,
                tokens=tokens,
            )
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度记录失败 (忽略)")

    @staticmethod
    def _check_usage_quota(user_id: str) -> None:
        try:
            from forge.quota import UserQuotaExceeded, get_usage_quota_manager

            get_usage_quota_manager().check_sync(user_id)
        except UserQuotaExceeded:
            _inc_budget_exceeded_metric(user_id)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("用户用量额度检查失败, 降级放行")

    @staticmethod
    def _key_str(key: tuple[str, str, str]) -> str:
        user_id, provider, model = key
        return f"{user_id or 'anon'}:{provider}:{model}"

    @staticmethod
    def _maybe_alert(
        label: str,
        user_id: str | None,
        used: float,
        limit: float,
        threshold: float,
    ) -> None:
        if limit <= 0 or threshold <= 0:
            return
        ratio = used / limit
        if ratio >= threshold and ratio < 1.0:
            logger.warning(
                "LLM 预算接近上限 %s: ratio=%.2f used=$%.4f limit=$%.4f user=%s",
                label,
                ratio,
                used,
                limit,
                user_id or "-",
            )

    @staticmethod
    def _extract(usage: dict | None, keys: tuple[str, ...]) -> int:
        if not usage:
            return 0
        for k in keys:
            v = usage.get(k)
            if isinstance(v, int | float):
                return int(v)
        return 0

    @staticmethod
    def _estimate_cost(model: str, prompt: int, completion: int) -> float:
        return estimate_cost(model, prompt, completion)


def estimate_cost(model: str, prompt: int, completion: int) -> float:
    """估算单次调用的 USD 成本. 给 audit logger / metrics 用 (无需持有 tracker)."""
    m = model.lower()
    for keyword, (in_price, out_price) in _PRICE_TABLE_USD.items():
        if keyword in m:
            return (prompt * in_price + completion * out_price) / 1_000_000
    return 0.0


def _inc_budget_exceeded_metric(user_id: str) -> None:
    """上报 budget exceeded 指标 (软依赖, 无 prometheus 时 no-op)."""
    try:
        from forge.observability.metrics import llm_metrics

        llm_metrics.inc_budget_exceeded(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("budget 指标上报失败 (忽略)")


# 全局单例
_tracker = CostTracker()


def get_cost_tracker() -> CostTracker:
    return _tracker
