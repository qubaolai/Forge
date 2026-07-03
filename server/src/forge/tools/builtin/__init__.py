"""内置工具.

import 触发各 builtin tool 的 @register_tool, 把它们注册到 ToolRegistry.
"""

from .file import read_document, read_file, write_file  # noqa: F401
from .knowledge import knowledge_search  # noqa: F401
from .message import read_message  # noqa: F401
from .time import time_tool
