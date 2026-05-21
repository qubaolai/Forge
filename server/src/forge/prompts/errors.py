"""PromptRegistry 异常."""

from __future__ import annotations


class PromptError(Exception):
    """所有 prompts 模块异常的基类."""


class TemplateNotFound(PromptError):
    """请求的模板不存在 (路径写错 / 文件未部署)."""


class TemplateRenderError(PromptError):
    """模板渲染失败 (变量名写错 / Jinja 语法错误等)."""
