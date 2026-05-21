"""提示词模板模块.

模板文件在 <repo_root>/prompts/, 代码在本模块.
对外只暴露 get_registry / init_registry / 异常类型.
"""

from .errors import PromptError, TemplateNotFound, TemplateRenderError
from .registry import PromptRegistry, get_registry, init_registry, reset_registry

__all__ = [
    "PromptError",
    "PromptRegistry",
    "TemplateNotFound",
    "TemplateRenderError",
    "get_registry",
    "init_registry",
    "reset_registry",
]
