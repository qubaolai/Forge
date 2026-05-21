"""结构化日志配置.

两种格式:
    - 文本 (默认, 终端友好): "<时间> [LEVEL] [trace_id] logger.name: message"
      自动启用 ANSI 彩色 (TTY 时), 输出到非 TTY (重定向到文件 / pipe) 时降级
      为纯文本, 避免日志里混杂转义码.
    - JSON (生产环境采集): 单行 JSON, 关键字段固定, 透传 extra={...}.

trace_id / user_id / client_type 由 TraceIdLogFilter 注入到每条 LogRecord.

启动时调一次 setup_logging() 即可, 替换默认的 basicConfig.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from datetime import datetime

from forge.api.middleware.tracing import TraceIdLogFilter

# 模块级过滤：早于 setup_logging() 调用，捕获 import 期间的 warnings
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

# ----------------------------------------------------------------------
# ANSI 配色
# ----------------------------------------------------------------------
# 终端 ANSI 转义码. 全部静态字符串, 不依赖第三方包.
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[1;41m",  # white on red bg
}
_LOGGER_NAME_COLOR = "\033[35m"  # magenta
_TRACE_ID_COLOR = "\033[36m"  # cyan dim
_TIME_COLOR = _DIM


def _supports_color() -> bool:
    """是否启用彩色: 必须是 TTY, 且 NO_COLOR 环境变量未设.

    显式开关 (优先级最高):
        FORCE_COLOR=1 -> 强制开
        NO_COLOR=非空 -> 强制关 (https://no-color.org/)
    """
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    stream = sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


# ----------------------------------------------------------------------
# 彩色文本 Formatter
# ----------------------------------------------------------------------
class ColorFormatter(logging.Formatter):
    """终端彩色 Formatter.

    着色策略:
        - 时间: 暗灰
        - LEVEL: 按级别 (DEBUG=cyan / INFO=green / WARNING=yellow / ERROR=red)
        - trace_id: 暗 cyan
        - logger name: magenta
        - message: 不着色 (避免读不清), ERROR 整行染红

    无颜色环境 (NO_COLOR / 非 TTY) 时, ANSI 串全部退化为空字符串.
    """

    def __init__(self, *, use_color: bool):
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        # 时间
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        ms = int(record.msecs)
        time_str = f"{ts},{ms:03d}"

        level = record.levelname
        trace_id = getattr(record, "trace_id", "-") or "-"
        name = record.name
        msg = record.getMessage()

        if self._use_color:
            level_color = _LEVEL_COLORS.get(level, "")
            line = (
                f"{_TIME_COLOR}{time_str}{_RESET} "
                f"{level_color}{_BOLD}{level:<8}{_RESET} "
                f"{_TRACE_ID_COLOR}[{trace_id}]{_RESET} "
                f"{_LOGGER_NAME_COLOR}{name}{_RESET}: "
                f"{msg}"
            )
        else:
            line = f"{time_str} [{level}] [{trace_id}] {name}: {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ----------------------------------------------------------------------
# JSON Formatter (生产采集)
# ----------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    """单行 JSON 格式化器, 关键字段固定, 其余走 extra."""

    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "trace_id",
            "user_id",
            "client_type",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat(timespec="milliseconds")
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "client_type": getattr(record, "client_type", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # 透传 extra={...}
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and not k.startswith("_"):
                payload[k] = v

        return json.dumps(payload, ensure_ascii=False, default=str)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def setup_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
    color: bool | None = None,
) -> None:
    """配置全局 logging.

    Args:
        level:       日志级别
        json_format: JSON 格式 (生产环境推荐 True, 配合 ELK / Loki 采集)
        color:       True/False 强制开关; None = 自动 (TTY 开, 非 TTY 关).
                     被 NO_COLOR / FORCE_COLOR 环境变量覆盖.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # 清掉默认 handler 防止重复
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdLogFilter())

    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        use_color = _supports_color() if color is None else bool(color)
        handler.setFormatter(ColorFormatter(use_color=use_color))

    # 第三方库会在自己 __init__ 里把 logger 重置为 DEBUG，setLevel 之后又被覆盖；
    # 用 handler 级过滤器确保这些 logger 的 DEBUG 消息不进入我们的输出。
    handler.addFilter(_ThirdPartyDebugFilter())
    root.addHandler(handler)


# -----------------------------------------------------------------------
# 第三方噪音压制
# -----------------------------------------------------------------------

# 模块导入时立即过滤 warnings（jieba _compat.py 在 import 时就会 warn）
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

# 这些第三方 logger 会自行将自己重置为 DEBUG；用 handler filter 而非 setLevel，
# 防止被覆盖。
_THIRD_PARTY_DEBUG_SUPPRESS = frozenset({"jieba", "httpx", "httpcore", "hpack", "multipart"})


class _ThirdPartyDebugFilter(logging.Filter):
    """丢弃已知高噪声第三方 logger 的 DEBUG 条目."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno <= logging.DEBUG:
            top = record.name.split(".")[0]
            if top in _THIRD_PARTY_DEBUG_SUPPRESS:
                return False
        return True
