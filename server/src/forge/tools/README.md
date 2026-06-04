# `forge.tools` — 工具系统

Agent 可调用能力的注册、调度与安全护栏。所有工具以 OpenAI function calling 的 schema 暴露给 LLM。

## 设计理念

1. **声明式元数据驱动调度与安全**：工具不只是一个函数，它在类上声明 `parallelism_safe` / `dangerous` / `required_scope` / `allowed_roles` / `path_role_whitelist`，调度器和 guardrail 据此决定并行/串行、审计、作用域校验。
2. **注册期预计算**：`@register_tool` 在注册时就把 JSON schema 缓存好，热路径零开销。
3. **CPU/IO 二选一**：实现 `run`（同步 CPU）或 `arun`（异步 IO）之一，基类统一适配。
4. **护栏与执行分离**：`ToolExecutor` 串起 access→permission→rate_limit→dangerous_op 流水线 + workspace 路径策略，工具本身不关心安全。

## 模块速览

```
tools/
├── base.py        ← Tool(ABC): 元数据字段 + run/arun 契约
├── registry.py    ← @register_tool 装饰器 + ToolRegistry (注册期 schema 缓存)
├── executor.py    ← ToolExecutor: guardrail 流水线 + 路径策略 + 并行调度
├── builtin/       ← 内置工具 (knowledge_search / time / file / shell / artifact / spawn_subagent ...)
├── mcp/           ← MCP (Model Context Protocol) 工具桥接
├── router/        ← 工具路由
└── sandbox/       ← 沙箱执行
```

## 如何使用

```python
from forge.tools import Tool, ToolRegistry, register_tool

@register_tool
class MyTool(Tool):
    name = "my_tool"
    description = "给 LLM 看的用途说明, 决定何时被调用"
    parameters = {"type": "object", "properties": {...}, "required": [...]}
    parallelism_safe = True          # 同 step 内可并行
    dangerous = False                # True → 进 audit.jsonl
    required_scope = "workspace"     # 需要 workspace 作用域(路径校验)

    async def arun(self, args: dict) -> dict:   # 或 def run(self, args)
        return {"ok": True, "result": ...}

# 取用
tool = ToolRegistry.get("my_tool")
all_tools = ToolRegistry.get_all()
```

## 如何扩展

- **加一个工具**：写 `Tool` 子类 + `@register_tool`，确保模块被 import（builtin 在包初始化时统一导入）。然后在对应 `agent_profile.tools_allowed` 里放行——**注册 ≠ 可用**，还要过 profile 白名单。
- **接入外部工具**：走 `mcp/` 桥接 MCP server。
- **自定义护栏**：扩展 `guardrails/tool/` 下的检查器，挂进 `ToolExecutor` 流水线。

## 边界与注意

- 工具可用性是两层门：①启动期 profile 校验工具已注册；②运行期 profile `tools_allowed` 白名单 + Plan Mode 动态工具集（写工具在 Plan 阶段物理不暴露）。
- `dangerous=True` 的工具调用会写 `audit.jsonl`。
- 子 agent 通过 `resolve_subagent_tools` 被 hard mask 掉写/创建/二级派发类工具（见 `builtin/agent/spawn.py`）。
