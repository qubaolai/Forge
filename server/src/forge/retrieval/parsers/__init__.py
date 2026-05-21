"""文档处理器包.

按文件格式组织各类 Parser, 通过 ``ParserDispatcher`` 统一调度.
新增格式时, 在对应子包下实现 Parser, 然后在 ``dispatcher.default_dispatcher``
中注册即可.

注意:
    这里只 re-export 轻量的 ParserDispatcher, 具体 parser 由 default_dispatcher()
    内部按需 import (缺第三方 SDK 时跳过该 parser, 不影响 import 包本身).
"""

from .dispatcher import ParserDispatcher, default_dispatcher

__all__ = [
    "ParserDispatcher",
    "default_dispatcher",
]
