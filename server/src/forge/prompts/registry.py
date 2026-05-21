"""PromptRegistry: 启动时一次性加载 prompts/*.j2, 运行时纯 render.

定位:
    - 数据 = 模板文件 (<repo_root>/prompts/**/*.j2)
    - 代码 = 本模块 (扫目录 / 编译 / render / 兜底)

设计要点:
    - StrictUndefined: 模板里写错变量名立刻抛 UndefinedError, 不静默替换.
    - render() 失败时返回 fallback 字符串 + log error + tracer.set_error,
      避免一处提示词坏字符串崩整个请求 (用户决定: b 方案兜底).
    - 用户的 agent.system_prompt 走 "纯文本" 通道:
      传给 render() 时作为变量值, 不会被 Jinja 二次解析,
      规避模板注入风险 (用户决定).
    - 不支持 hot reload (用户决定): 模板改动需重启服务.

模板根目录:
    默认: <forge 包目录>/../../prompts  即仓库根 prompts/
    Override: 环境变量 PROMPTS_DIR=/absolute/path 或 init_registry(template_root=...)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import jinja2

from forge.observability.tracing.tracer import span

from .errors import TemplateNotFound, TemplateRenderError

logger = logging.getLogger(__name__)


# 渲染失败兜底文本: 极简, 确保对话能继续, 但完全失去定制能力.
_FALLBACK_TEXT = "你是一个 helpful 的 AI 助手. 请回答用户的最新一条消息."


def _default_template_root() -> Path:
    """定位仓库根 prompts/ 目录.

    优先级:
        1. PROMPTS_DIR 环境变量
        2. src/forge/prompts/registry.py 出发, 上溯 3 级 + /prompts
    """
    env = os.environ.get("PROMPTS_DIR")
    if env:
        return Path(env).resolve()
    # registry.py -> prompts/ -> forge/ -> src/ -> <root>
    return Path(__file__).resolve().parents[3] / "prompts"


class PromptRegistry:
    """启动时扫一遍, 运行时只 render."""

    def __init__(self, template_root: Path | None = None) -> None:
        self._root = (template_root or _default_template_root()).resolve()
        if not self._root.exists():
            raise FileNotFoundError(f"prompts 模板根目录不存在: {self._root}")
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._root)),
            undefined=jinja2.StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
            autoescape=False,  # 提示词非 HTML, 不要 escape
        )
        self._compiled: dict[str, jinja2.Template] = {}
        self._eager_compile()
        logger.info(
            "提示词加载完成: root=%s, templates=%d",
            self._root,
            len(self._compiled),
        )

    # ------------------------------------------------------------------
    # 启动期: 扫目录 + 编译所有 .j2
    # ------------------------------------------------------------------
    def _eager_compile(self) -> None:
        for path in sorted(self._root.rglob("*.j2")):
            name = path.relative_to(self._root).with_suffix("").as_posix()
            try:
                self._compiled[name] = self._env.get_template(
                    path.relative_to(self._root).as_posix()
                )
            except jinja2.TemplateSyntaxError as e:
                # 启动期模板语法错误属于严重 bug, 直接抛.
                raise TemplateRenderError(f"模板语法错误 {name}: {e}") from e

    # ------------------------------------------------------------------
    # 业务 API
    # ------------------------------------------------------------------
    def exists(self, name: str, /) -> bool:
        return name in self._compiled

    def render(self, name: str, /, **vars) -> str:
        """渲染指定模板.

        Args:
            name: 模板路径 (不含 .j2 后缀), 例如 "chat/default_system".
                  positional-only, 避免与模板里的 {{ name }} 变量同名冲突.
            **vars: 模板变量.

        Returns:
            渲染后的字符串. 失败时返回 _FALLBACK_TEXT (不抛, 保证主流程能继续).
        """
        with span("prompt.render", template=name) as s:
            tpl = self._compiled.get(name)
            if tpl is None:
                not_found = TemplateNotFound(f"模板不存在: {name}")
                logger.error("提示词渲染失败, 走兜底文本: %s", not_found)
                s.set_error(not_found)
                s.set("fallback", True)
                return _FALLBACK_TEXT
            try:
                text = tpl.render(**vars)
                s.set("rendered_chars", len(text))
                return text
            except jinja2.UndefinedError as e:
                render_error = TemplateRenderError(f"模板变量错误 {name}: {e}")
                logger.error("提示词渲染失败, 走兜底文本: %s", render_error)
                s.set_error(render_error)
                s.set("fallback", True)
                return _FALLBACK_TEXT
            except jinja2.TemplateError as e:
                render_error = TemplateRenderError(f"模板渲染失败 {name}: {e}")
                logger.error("提示词渲染失败, 走兜底文本: %s", render_error)
                s.set_error(render_error)
                s.set("fallback", True)
                return _FALLBACK_TEXT


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_registry: PromptRegistry | None = None
_lock = threading.Lock()


def init_registry(template_root: Path | None = None) -> PromptRegistry:
    """显式初始化全局单例 (一般在应用 lifespan startup 阶段调用一次)."""
    global _registry
    with _lock:
        _registry = PromptRegistry(template_root)
        return _registry


def get_registry() -> PromptRegistry:
    """获取全局单例. 未显式初始化时按默认路径懒初始化.

    懒初始化是为了支持脚本 / 单测里不走 FastAPI lifespan 也能用 Registry.
    """
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = PromptRegistry()
    return _registry


def reset_registry() -> None:
    """主要给单测用, 释放单例后下次访问会重新加载."""
    global _registry
    with _lock:
        _registry = None
