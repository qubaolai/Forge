"""知识库相关内置工具.

工具列表:
    - knowledge_search: 在用户指定的 KB 中检索相关文档片段

可用 KB 列表通过 chat system prompt 注入 (见 chat/assembler.py), 不再
单独提供 list_knowledge_bases 工具, 省一次 LLM round-trip.
"""

from . import knowledge_search  # noqa: F401
