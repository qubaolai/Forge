"""OpenAI / Claude Code 风格的用户用量额度.

额度按 LLM 成本流水计量, 支持两个默认滚动窗口:
    - five_hour: 最近 5 小时
    - weekly: 最近 7 天

每个窗口可按 USD / tokens / calls 配一个或多个上限。任意上限耗尽,
后续 LLM 调用会在调用前被阻断。这里刻意不再统计 chat turn 的墙钟耗时。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from forge.config.domains.quota import UsageQuotaWindowSettings, UserQuotaSettings
from forge.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageQuotaEvent:
    """一笔已知的模型用量."""

    user_id: str
    cost_usd: float = 0.0
    tokens: int = 0
    calls: int = 1
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class UsageQuotaWindowStatus:
    """某个滚动窗口下的用量与限制."""

    name: str
    window_seconds: int
    window_start: datetime
    window_end: datetime
    used_usd: float
    used_tokens: int
    used_calls: int
    limit_usd: float | None
    limit_tokens: int | None
    limit_calls: int | None
    alert_threshold: float

    @property
    def exceeded(self) -> bool:
        return (
            (self.limit_usd is not None and self.used_usd >= self.limit_usd)
            or (self.limit_tokens is not None and self.used_tokens >= self.limit_tokens)
            or (self.limit_calls is not None and self.used_calls >= self.limit_calls)
        )

    @property
    def near_limit(self) -> bool:
        return any(
            ratio >= self.alert_threshold
            for ratio in (
                self.used_usd / self.limit_usd if self.limit_usd else 0.0,
                self.used_tokens / self.limit_tokens if self.limit_tokens else 0.0,
                self.used_calls / self.limit_calls if self.limit_calls else 0.0,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window_seconds": self.window_seconds,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "used_usd": round(self.used_usd, 6),
            "used_tokens": self.used_tokens,
            "used_calls": self.used_calls,
            "limit_usd": self.limit_usd,
            "limit_tokens": self.limit_tokens,
            "limit_calls": self.limit_calls,
            "remaining_usd": (
                round(max(0.0, self.limit_usd - self.used_usd), 6)
                if self.limit_usd is not None
                else None
            ),
            "remaining_tokens": (
                max(0, self.limit_tokens - self.used_tokens)
                if self.limit_tokens is not None
                else None
            ),
            "remaining_calls": (
                max(0, self.limit_calls - self.used_calls) if self.limit_calls is not None else None
            ),
            "near_limit": self.near_limit,
            "exceeded": self.exceeded,
        }


@dataclass(frozen=True)
class UsageQuotaStatus:
    """用户整体额度状态."""

    enabled: bool
    user_id: str
    generated_at: datetime
    windows: tuple[UsageQuotaWindowStatus, ...]

    @property
    def exceeded(self) -> bool:
        return self.enabled and any(w.exceeded for w in self.windows)

    @property
    def near_limit(self) -> bool:
        return self.enabled and any(w.near_limit for w in self.windows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "user_id": self.user_id,
            "generated_at": self.generated_at.isoformat(),
            "near_limit": self.near_limit,
            "exceeded": self.exceeded,
            "windows": [w.to_dict() for w in self.windows],
        }


class UserQuotaExceeded(Exception):
    """用户滚动用量额度已耗尽."""

    def __init__(self, status: UsageQuotaStatus) -> None:
        self.status = status
        exceeded = next((w for w in status.windows if w.exceeded), None)
        if exceeded is None:
            message = "用户用量额度已用尽"
        else:
            details: list[str] = []
            if exceeded.limit_usd is not None:
                details.append(f"cost ${exceeded.used_usd:.4f}/${exceeded.limit_usd}")
            if exceeded.limit_tokens is not None:
                details.append(f"tokens {exceeded.used_tokens}/{exceeded.limit_tokens}")
            if exceeded.limit_calls is not None:
                details.append(f"calls {exceeded.used_calls}/{exceeded.limit_calls}")
            message = f"用户 {status.user_id} 的 {exceeded.name} 用量额度已用尽: " + ", ".join(
                details
            )
        super().__init__(message)


class UsageQuotaManager:
    """滚动窗口额度管理器.

    `check_sync()` 走启动期 / flush 期装载的 cost.jsonl baseline + 当前进程内
    还未 flush 的 delta, 因而可以在 LLM 调用前同步阻断。
    `status()` 是 API 查询路径, 会直接扫描 cost.jsonl 以返回最新持久化视图。
    """

    def __init__(self) -> None:
        self._settings = UserQuotaSettings()
        self._memory_events: list[UsageQuotaEvent] = []
        self._baseline_events: list[UsageQuotaEvent] = []
        self._lock = threading.Lock()

    def configure(self, settings: UserQuotaSettings) -> None:
        with self._lock:
            self._settings = settings
            self._prune_locked(datetime.now(UTC))

    def record(
        self,
        *,
        user_id: str,
        cost_usd: float,
        tokens: int,
        calls: int = 1,
        ts: datetime | None = None,
    ) -> None:
        """记录当前进程内尚未 flush 的模型用量."""
        if not user_id or calls <= 0:
            return
        with self._lock:
            if not self._settings.enabled or not self._settings.has_limits:
                return
            self._memory_events.append(
                UsageQuotaEvent(
                    user_id=user_id,
                    cost_usd=max(0.0, float(cost_usd or 0.0)),
                    tokens=max(0, int(tokens or 0)),
                    calls=int(calls),
                    ts=_ensure_aware(ts or datetime.now(UTC)),
                )
            )
            self._prune_locked(datetime.now(UTC))

    def mark_persisted(self, cutoff: datetime) -> None:
        """CostTracker flush 成功后移除已写入 cost.jsonl 的内存事件."""
        cutoff = _ensure_aware(cutoff)
        with self._lock:
            self._memory_events = [e for e in self._memory_events if e.ts > cutoff]

    def check_sync(self, user_id: str | None) -> None:
        """在 LLM 调用前同步检查额度."""
        if not user_id:
            return
        with self._lock:
            settings = self._settings
            if not settings.enabled or not settings.has_limits:
                return
            now = datetime.now(UTC)
            self._prune_locked(now)
            status = _build_status(
                user_id=user_id,
                settings=settings,
                events=[*self._baseline_events, *self._memory_events],
                now=now,
            )

        if status.exceeded:
            raise UserQuotaExceeded(status)
        if status.near_limit:
            logger.warning(
                "用户用量额度接近上限 user=%s status=%s",
                user_id,
                status.to_dict(),
            )

    async def status(self, user_id: str) -> UsageQuotaStatus:
        """返回用户当前额度状态. API 查询路径使用."""
        with self._lock:
            settings = self._settings
            memory_events = list(self._memory_events)
        if not settings.enabled or not settings.has_limits:
            return _build_status(
                user_id=user_id,
                settings=settings,
                events=[],
                now=datetime.now(UTC),
            )

        persisted = await _events_from_cost_log(settings)
        with self._lock:
            memory_events = list(self._memory_events)
        return _build_status(
            user_id=user_id,
            settings=settings,
            events=[*persisted, *memory_events],
            now=datetime.now(UTC),
        )

    async def hydrate_baseline(self) -> None:
        """从 cost.jsonl 装载滚动窗口 baseline."""
        with self._lock:
            settings = self._settings
        events = await _events_from_cost_log(settings)
        with self._lock:
            self._baseline_events = events
            self._prune_locked(datetime.now(UTC))
        logger.info("用户用量额度 baseline 装载完成 events=%d", len(events))

    def reset(self) -> None:
        with self._lock:
            self._memory_events.clear()
            self._baseline_events.clear()
            self._settings = UserQuotaSettings()

    def _prune_locked(self, now: datetime) -> None:
        max_window = _max_window_seconds(self._settings)
        if max_window <= 0:
            return
        cutoff = _ensure_aware(now) - timedelta(seconds=max_window)
        self._memory_events = [e for e in self._memory_events if e.ts >= cutoff]
        self._baseline_events = [e for e in self._baseline_events if e.ts >= cutoff]


async def _events_from_cost_log(settings: UserQuotaSettings) -> list[UsageQuotaEvent]:
    if not settings.enabled or not settings.has_limits:
        return []

    from forge.infrastructure.cost_log import default_cost_log

    since = datetime.now(UTC) - timedelta(seconds=_max_window_seconds(settings))
    events: list[UsageQuotaEvent] = []
    async for entry in default_cost_log().iter_all():
        if entry.error or not entry.user_id:
            continue
        ts = _ensure_aware(entry.ts)
        if ts < since:
            continue
        events.append(
            UsageQuotaEvent(
                user_id=entry.user_id,
                cost_usd=float(entry.cost_usd or 0.0),
                tokens=int(entry.prompt_tokens or 0) + int(entry.completion_tokens or 0),
                calls=max(
                    0,
                    int(entry.call_count or 1) - int(entry.error_count or 0),
                ),
                ts=ts,
            )
        )
    return events


def _build_status(
    *,
    user_id: str,
    settings: UserQuotaSettings,
    events: list[UsageQuotaEvent],
    now: datetime,
) -> UsageQuotaStatus:
    now = _ensure_aware(now)
    windows = (
        _window_status(
            "five_hour",
            settings.five_hour,
            user_id=user_id,
            events=events,
            now=now,
            alert_threshold=settings.alert_threshold,
        ),
        _window_status(
            "weekly",
            settings.weekly,
            user_id=user_id,
            events=events,
            now=now,
            alert_threshold=settings.alert_threshold,
        ),
    )
    return UsageQuotaStatus(
        enabled=settings.enabled,
        user_id=user_id,
        generated_at=now,
        windows=windows,
    )


def _window_status(
    name: str,
    cfg: UsageQuotaWindowSettings,
    *,
    user_id: str,
    events: list[UsageQuotaEvent],
    now: datetime,
    alert_threshold: float,
) -> UsageQuotaWindowStatus:
    start = now - timedelta(seconds=cfg.window_seconds)
    used_usd = 0.0
    used_tokens = 0
    used_calls = 0
    for event in events:
        if event.user_id != user_id:
            continue
        if not start <= _ensure_aware(event.ts) <= now:
            continue
        used_usd += event.cost_usd
        used_tokens += event.tokens
        used_calls += event.calls
    return UsageQuotaWindowStatus(
        name=name,
        window_seconds=cfg.window_seconds,
        window_start=start,
        window_end=now,
        used_usd=used_usd,
        used_tokens=used_tokens,
        used_calls=used_calls,
        limit_usd=cfg.limit_usd,
        limit_tokens=cfg.limit_tokens,
        limit_calls=cfg.limit_calls,
        alert_threshold=alert_threshold,
    )


def _max_window_seconds(settings: UserQuotaSettings) -> int:
    return max(settings.five_hour.window_seconds, settings.weekly.window_seconds)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


_manager = UsageQuotaManager()


def get_usage_quota_manager() -> UsageQuotaManager:
    return _manager


def configure_usage_quota(settings: Settings | None = None) -> UsageQuotaManager:
    settings = settings or get_settings()
    manager = get_usage_quota_manager()
    manager.configure(settings.user_quota)
    return manager


__all__ = [
    "UsageQuotaEvent",
    "UsageQuotaManager",
    "UsageQuotaStatus",
    "UsageQuotaWindowStatus",
    "UserQuotaExceeded",
    "configure_usage_quota",
    "get_usage_quota_manager",
]
