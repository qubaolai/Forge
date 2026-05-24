"""TaskRunner — 用 ReActAgent 真实执行 TaskNode (B9/P0-1).

设计要点
- 每个 TaskNode 实例化一个 ReActAgent，``tools`` 严格限制为
  ``node.allowed_tools`` 与 ToolRegistry 的交集；``max_steps=node.max_steps``。
- WRITE 任务：调用方先 prepare 隔离 worktree，再把 ``work_path`` 传进来；
  TaskRunner 在 worktree 下执行 ReAct（通过 ``os.chdir`` + 全局 lock 实现，
  内置 file 工具基于 ``Path.cwd()`` 解析路径）。lock 保证 chdir 不冲突。
- 其他 kind 直接在 workspace 主目录里跑。
- 任何 LLM / 工具异常都向上抛 ``TaskRunnerError``，由 Executor 转 FAILED。

约束
- 仅在 ``enable_real_llm=true`` 时由 supervisor 注入这条路径；
  默认仍走 ``_build_artifact_payload`` 占位，保持单测和零依赖体验。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass

from forge.adaptive.models import TaskKind, TaskNode

logger = logging.getLogger(__name__)


# 全局 chdir lock —— wave 内并发 WRITE 任务通过 worktree 隔离避免文件冲突，
# 但 process cwd 是全局态，必须串行切换。READ 任务不切 cwd 因此不受影响。
_CHDIR_LOCK = asyncio.Lock()


class TaskRunnerError(RuntimeError):
    """TaskNode 真实执行失败。"""


@dataclass(frozen=True)
class TaskRunResult:
    """单个 TaskNode 真实执行的结果。"""

    output: str
    steps: int
    usage: dict


_KIND_HINTS = {
    TaskKind.READ: "你处于只读模式：禁止任何写操作。汇报你发现的事实。",
    TaskKind.WRITE: (
        "你在隔离 worktree 中工作，可以读写 write_scope 内的文件。"
        "完成后输出本次改动的文件清单与变更摘要。"
    ),
    TaskKind.EXECUTE: "执行声明的命令或测试，并报告退出码 + 关键输出。",
    TaskKind.REVIEW: "只读评审：基于已有上下文给出结论与建议。",
    TaskKind.INTEGRATE: "合并多个上游产物，必要时调用写工具落盘。",
}


def _build_task_system_prompt(node: TaskNode, workspace_path: str) -> str:
    """B15: 优先 prompts/adaptive/task_runner.j2，失败回退内联文本。"""
    hint = _KIND_HINTS.get(node.kind, "")
    read_scope_text = str(list(node.read_scope)) if node.read_scope else "(全工作区)"
    write_scope_text = ", ".join(node.write_scope) if node.write_scope else "(无)"
    acceptance_text = node.acceptance_criteria or "(自行判断完成度)"

    try:
        from forge.prompts import get_registry

        registry = get_registry()
        if registry.exists("adaptive/task_runner"):
            return registry.render(
                "adaptive/task_runner",
                workspace_path=workspace_path,
                task_id=node.id,
                title=node.title,
                kind=node.kind.value,
                allowed_tools=list(node.allowed_tools),
                read_scope_text=read_scope_text,
                write_scope_text=write_scope_text,
                acceptance_criteria_text=acceptance_text,
                kind_hint=hint,
            )
    except Exception:
        logger.exception("TaskRunner prompt 模板渲染失败，使用 fallback")

    return (
        f"你是 Forge adaptive run 中的 TaskAgent。\n"
        f"工作区: {workspace_path}\n"
        f"任务 id: {node.id}\n"
        f"标题: {node.title}\n"
        f"类型: {node.kind.value}\n"
        f"允许工具: {list(node.allowed_tools)}\n"
        f"读取作用域: {read_scope_text}\n"
        f"写入作用域: {write_scope_text}\n"
        f"验收标准: {acceptance_text}\n\n"
        f"## 行为约束\n- {hint}\n"
        f"- 严禁越界访问 read_scope / write_scope 之外的路径。\n"
        f"- 完成后用一段中文文字总结你做了什么、对应文件、留下的注意点。"
    )


@contextmanager
def _chdir(path: str):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        try:
            os.chdir(prev)
        except OSError:
            logger.warning("恢复 cwd 失败 prev=%s", prev)


async def run_node_with_react(
    *,
    node: TaskNode,
    workspace_path: str,
    work_path: str | None,
    upstream_context: str = "",
    model_profile_resolver=None,
) -> TaskRunResult:
    """用 ReActAgent 跑一个 TaskNode。

    Args:
        node: TaskNode 元数据
        workspace_path: 工作区主路径（READ/EXECUTE 等使用）
        work_path: WRITE 任务的隔离 worktree 路径；非 WRITE 传 None
        upstream_context: 上游 artifact 拼接的上下文文本（goal + dep summary）
        model_profile_resolver: 函数 ``profile -> model_id``，None 时使用 settings 默认

    Returns:
        TaskRunResult

    Raises:
        TaskRunnerError: 任何 LLM/工具异常都抛出，由 Executor 转 TaskStatus.FAILED
    """
    from forge.config.settings import get_settings

    from forge.agents.react.agent import ReActAgent
    from forge.llm.gateway import build_chain_from_settings, split_provider_model
    from forge.tools.registry import ToolRegistry

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        raise TaskRunnerError(f"settings 加载失败: {exc}") from exc

    # 选 model
    target_provider: str | None = None
    target_model: str | None = None
    profiles = getattr(getattr(settings, "task_execution", None), "model_profiles", None)
    if model_profile_resolver is not None:
        target_provider, target_model = split_provider_model(
            model_profile_resolver(node.model_profile)
        )
    elif profiles is not None:
        raw_model = (
            profiles.get(node.model_profile)
            if isinstance(profiles, dict)
            else getattr(profiles, node.model_profile, None)
        )
        target_provider, target_model = split_provider_model(str(raw_model) if raw_model else None)
    if not target_provider or not target_model:
        raise TaskRunnerError(
            f"任务 {node.id} 的模型档位 {node.model_profile!r} 必须配置为 provider:model"
        )
    try:
        chain = await build_chain_from_settings(
            settings, provider=target_provider, model=target_model
        )
    except Exception as exc:  # noqa: BLE001
        raise TaskRunnerError(f"LLM chain 构造失败: {exc}") from exc

    # 工具白名单（与 node.allowed_tools 取交集）
    try:
        all_tools = ToolRegistry.get_all()
        wanted = set(node.allowed_tools)
        tools = [t for t in all_tools if t.name in wanted]
    except Exception as exc:  # noqa: BLE001
        raise TaskRunnerError(f"工具准备失败: {exc}") from exc
    if not tools:
        raise TaskRunnerError(f"任务 {node.id} 允许工具集合为空，无法执行")

    # C9/fix-4: 给 ReActAgent 注入一个 per-task ToolExecutor，
    # 把 write_scope / read_scope 在工具执行层做硬约束，不再依赖 prompt 自律。
    from forge.tools.executor import ToolExecutor as _PerTaskToolExecutor

    per_task_executor = _PerTaskToolExecutor(
        task_workspace_root=work_path or workspace_path,
        task_write_scope=tuple(node.write_scope) if node.write_scope else None,
        task_read_scope=tuple(node.read_scope) if node.read_scope else None,
    )

    agent = ReActAgent(
        llm=chain,
        tools=tools,
        system_prompt=_build_task_system_prompt(node, workspace_path),
        max_steps=node.max_steps,
        role=f"adaptive:{node.kind.value}",
        executor=per_task_executor,
    )

    user_input_parts = [f"任务目标: {node.title}"]
    if node.kind == TaskKind.EXECUTE and node.command:
        user_input_parts.append(f"建议执行命令: {node.command}")
    if upstream_context:
        user_input_parts.append("## 上游上下文\n" + upstream_context)
    user_input = "\n\n".join(user_input_parts)

    # WRITE：在 worktree 内 chdir 执行；其它：在主 workspace 内 chdir
    target_cwd = work_path if (node.kind == TaskKind.WRITE and work_path) else workspace_path

    async with _CHDIR_LOCK:
        try:
            def _run_sync() -> TaskRunResult:
                with _chdir(target_cwd):
                    result = agent.run(user_input)
                return TaskRunResult(
                    output=str(result.output or ""),
                    steps=int(result.steps),
                    usage=dict(result.usage or {}),
                )

            return await asyncio.to_thread(_run_sync)
        except Exception as exc:  # noqa: BLE001
            raise TaskRunnerError(f"ReActAgent 执行异常: {exc}") from exc
