"""时间处理工具.

支持:
- 当前时间/时区转换/格式化
- 时间偏移 (天/小时/分钟/秒)
- 相对时间关键词: 上周/下周/本周, 上月/下月/本月, 去年/明年/今年, 昨天/明天等
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from forge.tools.base import Tool
from forge.tools.registry import register_tool

_DEFAULT_FMT = "%Y-%m-%d %H:%M:%S %Z%z"


@register_tool
class TimeTool(Tool):
    name = "time_tool"
    description = (
        "获取和计算时间，支持时区转换、相对时间关键词(如下周/上月)和格式化输出。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA 时区名，如 Asia/Shanghai。",
            },
            "output": {
                "type": "string",
                "enum": ["all", "iso", "timestamp", "timestamp_ms", "formatted"],
                "description": "返回类型，默认 all。",
            },
            "format": {
                "type": "string",
                "description": "output=formatted/all 时使用的 strftime 格式。",
            },
            "base_timestamp": {
                "type": "number",
                "description": "基准 Unix 时间戳（秒），不传默认当前时间。",
            },
            "relative": {
                "type": "string",
                "description": (
                    "相对时间关键词，如 今天/明天/昨天/后天/前天/"
                    "本周/上周/下周/本月/上月/下月/今年/去年/明年"
                ),
            },
            "relative_point": {
                "type": "string",
                "enum": ["current", "start", "end"],
                "description": "周期词(周/月/年)的取点，默认 current。",
            },
            "week_start": {
                "type": "string",
                "enum": ["monday", "sunday"],
                "description": "周起始日，默认 monday。",
            },
            "add_seconds": {"type": "integer", "description": "偏移秒，可负数。"},
            "add_minutes": {"type": "integer", "description": "偏移分钟，可负数。"},
            "add_hours": {"type": "integer", "description": "偏移小时，可负数。"},
            "add_days": {"type": "integer", "description": "偏移天，可负数。"},
        },
        "required": [],
    }
    parallelism_safe = True

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        tz_name = str(args.get("timezone") or "").strip()
        output = str(args.get("output") or "all").strip().lower()
        fmt = str(args.get("format") or _DEFAULT_FMT)
        relative = str(args.get("relative") or "").strip()
        relative_point = str(args.get("relative_point") or "current").strip().lower()
        week_start = str(args.get("week_start") or "monday").strip().lower()

        tz, tz_err = _resolve_tz(tz_name)
        if tz_err:
            return {"ok": False, "error": tz_err}

        base_ts = args.get("base_timestamp")
        if base_ts is None:
            dt = datetime.now(tz)
        else:
            try:
                dt = datetime.fromtimestamp(float(base_ts), tz=tz)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"base_timestamp 非法: {exc}"}

        if relative:
            dt_rel, rel_err = _apply_relative(dt, relative, relative_point, week_start)
            if rel_err:
                return {"ok": False, "error": rel_err}
            dt = dt_rel

        try:
            dt = dt + timedelta(
                days=int(args.get("add_days") or 0),
                hours=int(args.get("add_hours") or 0),
                minutes=int(args.get("add_minutes") or 0),
                seconds=int(args.get("add_seconds") or 0),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"时间偏移参数非法: {exc}"}

        if output == "iso":
            return {"ok": True, "timezone": str(tz), "value": dt.isoformat()}
        if output == "timestamp":
            return {"ok": True, "timezone": str(tz), "value": dt.timestamp()}
        if output == "timestamp_ms":
            return {"ok": True, "timezone": str(tz), "value": int(dt.timestamp() * 1000)}
        if output == "formatted":
            try:
                return {"ok": True, "timezone": str(tz), "value": dt.strftime(fmt)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"format 非法: {exc}"}
        if output != "all":
            return {"ok": False, "error": f"不支持的 output: {output}"}

        try:
            formatted = dt.strftime(fmt)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"format 非法: {exc}"}

        offset = dt.utcoffset()
        return {
            "ok": True,
            "timezone": str(tz),
            "iso": dt.isoformat(),
            "timestamp": dt.timestamp(),
            "timestamp_ms": int(dt.timestamp() * 1000),
            "formatted": formatted,
            "offset": offset.total_seconds() if offset else 0,
        }


def _resolve_tz(name: str):
    if not name:
        return datetime.now().astimezone().tzinfo, None
    try:
        return ZoneInfo(name), None
    except ZoneInfoNotFoundError:
        return None, f"未知时区: {name}"


def _apply_relative(
    base: datetime,
    relative: str,
    point: str,
    week_start: str,
) -> tuple[datetime, str | None]:
    if point not in {"current", "start", "end"}:
        return base, f"relative_point 不支持: {point}"

    rel = relative.strip().lower()
    mapping = {
        "today": "今天",
        "tomorrow": "明天",
        "yesterday": "昨天",
        "day_after_tomorrow": "后天",
        "day_before_yesterday": "前天",
        "this_week": "本周",
        "last_week": "上周",
        "next_week": "下周",
        "this_month": "本月",
        "last_month": "上月",
        "next_month": "下月",
        "this_year": "今年",
        "last_year": "去年",
        "next_year": "明年",
    }
    rel = mapping.get(rel, relative)

    if rel == "今天":
        return base, None
    if rel == "明天":
        return base + timedelta(days=1), None
    if rel == "昨天":
        return base - timedelta(days=1), None
    if rel == "后天":
        return base + timedelta(days=2), None
    if rel == "前天":
        return base - timedelta(days=2), None

    if rel in {"本周", "上周", "下周"}:
        monday_idx = 0 if week_start == "monday" else 6
        days_from_week_start = (base.weekday() - monday_idx) % 7
        start = base - timedelta(days=days_from_week_start)
        if rel == "上周":
            start -= timedelta(days=7)
        elif rel == "下周":
            start += timedelta(days=7)
        end = start + timedelta(days=6)
        return _select_point(base, start, end, point), None

    if rel in {"本月", "上月", "下月"}:
        y, m = base.year, base.month
        if rel == "上月":
            y, m = _add_month(y, m, -1)
        elif rel == "下月":
            y, m = _add_month(y, m, 1)
        start = base.replace(year=y, month=m, day=1)
        last_day = calendar.monthrange(y, m)[1]
        end = base.replace(year=y, month=m, day=last_day)
        return _select_point(base, start, end, point), None

    if rel in {"今年", "去年", "明年"}:
        y = base.year + (-1 if rel == "去年" else 1 if rel == "明年" else 0)
        start = base.replace(year=y, month=1, day=1)
        end = base.replace(year=y, month=12, day=31)
        return _select_point(base, start, end, point), None

    return base, f"不支持的 relative 值: {relative}"


def _select_point(base: datetime, start: datetime, end: datetime, point: str) -> datetime:
    if point == "current":
        return base.replace(year=start.year, month=start.month, day=start.day)
    if point == "start":
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    return end.replace(hour=23, minute=59, second=59, microsecond=999999)


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    new_year = idx // 12
    new_month = idx % 12 + 1
    return new_year, new_month
