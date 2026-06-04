# `forge.workspace` — 工作区加载与运行时策略

CLI 路径（plan_exec / workflow）的工作区概念：把一个目录解析成运行时设置 + 工具路径策略，决定 agent 能在哪些路径读写。

## 设计理念

1. **路径即边界**：工作区把「能改哪些目录」显式化为 `ToolRuntimePolicy`（绝对化的可写根路径集合），工具执行时据此做路径校验——安全由代码而非 prompt 保证。
2. **运行时设置可覆盖**：`WorkspaceRuntimeSettings` 允许按工作区覆盖默认（如 LoopGuard 的步数/墙钟阈值），并对非法配置回退到安全默认。
3. **加载与运行分离**：`loader.py` 负责发现/解析工作区，`runtime.py` 负责把它解析成策略对象。

## 模块速览

```
workspace/
├── loader.py    ← 工作区加载/初始化
└── runtime.py   ← WorkspaceRuntimeSettings / ToolRuntimePolicy
                   + resolve_runtime_settings / resolve_tool_runtime_policy
```

## 如何使用

```python
from forge.workspace.runtime import resolve_runtime_settings, resolve_tool_runtime_policy
settings = resolve_runtime_settings(workspace_path)        # 运行时设置(含降级保护)
policy = resolve_tool_runtime_policy(workspace_path)       # 工具路径策略(绝对根)
```

由 `RunOrchestrator` 在 CLI run 装配阶段调用，喂给 `ToolExecutor` 的路径策略。

## 如何扩展

- **加运行时可覆盖项**：在 `WorkspaceRuntimeSettings` 加字段并在 `resolve_runtime_settings` 解析（注意非法值回退默认）。
- **改路径策略语义**：扩展 `resolve_tool_runtime_policy`（如多根、只读根）。

## 边界与注意

- 路径策略是 CLI 写工具的最后一道物理边界，配合 Plan Mode 动态工具集与角色 `write_path_prefixes`。
- 非法运行时配置不应让 run 起不来——一律回退到安全默认并告警。
