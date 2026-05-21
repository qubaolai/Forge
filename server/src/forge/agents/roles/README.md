# Agent Roles

P3 提供 7 个预置角色，角色定义由 `factory.py` 注册，prompt 模板位于 `prompts/roles/`。

## 预置角色

| Role | Prompt | 写权限 | max_steps | 默认模型 | 退化模型 |
|---|---|---:|---:|---|---|
| `triage` | `roles/triage` | 无 | 10 | `sonnet-4-6` | `gpt-4o` |
| `ra` | `roles/ra` | 无 | 10 | `gpt-4o` | `gpt-4o-mini` |
| `architect` | `roles/architect` | 无 | 15 | `opus-4-7` | `sonnet-4-6` |
| `developer` | `roles/developer` | `src/`, `app/`, `lib/` | 30 | `sonnet-4-6` | `gpt-4o` |
| `reviewer` | `roles/reviewer` | 无 | 15 | `opus-4-7` | `sonnet-4-6` |
| `qa` | `roles/qa` | `tests/` | 20 | `gpt-4o` | `gpt-4o-mini` |
| `devops` | `roles/devops` | `.github/`, `docker/`, `infra/` | 25 | `gpt-4o-mini` | `gpt-4o` |

## 扩展方式

1. 增加 prompt 模板到 `prompts/roles/{name}.j2`。
2. 构造 `AgentRole`，字段必须包含工具白名单、模型偏好、写权限前缀和 `max_steps`。
3. 调用 `register_custom_agent_role(role)` 注册当前进程内动态角色。
4. 如需持久化动态角色，后续应接入配置层或 DB；不要直接改 `AGENT_ROLES`。

## 运行时规则

角色选择发生在 workflow phase 执行时。`ChatTurnPhaseExecutor` 将 role prompt、工具白名单、模型偏好、`max_steps` 注入现有 `TurnOrchestrator`，复用同一个 `ReActAgent` 实现。

## Subagent 边界 (S6.5 M2)

`spawn_subagent` 出来的子 agent 在工具层做 **hard mask**, 不是 prompt 自律. 强制规则:

1. **被剥离的工具** (`SUBAGENT_DENY_TOOLS`, 见 [spawn.py](../../tools/builtin/agent/spawn.py)):

   | 工具 | 原因 |
   |---|---|
   | `write_file` / `edit_file` / `shell` | 子 agent 不允许改 workspace |
   | `create_artifact` | 共享空间创建权限只属于父; 子只能 `get_artifact / search_artifact` 读 |
   | `delegate_to_agent` / `spawn_subagent` | 二级派发与"父调度"模型冲突, 嵌套深度 ≤ 2 上限也由 `SUBAGENT_DEPTH` ContextVar 兜底 |

2. **可用的工具** = `role.allowed_tools − SUBAGENT_DENY_TOOLS`. 子 agent 即使从父 role
   继承到 write 权限, 通过 `resolve_subagent_tools()` 在装配 `ReActAgent` 前就被剥光.

3. **通信信道**: 子 agent system prompt 末尾追加固定的 `SUBAGENT_CHANNEL_NOTICE`,
   显式声明"只能通过返回字符串与父通信". 7 个 role 模板都有 `{{ subagent_channel_notice }}`
   占位; 父 agent 渲染时传空串, spawn 时传该常量, 模板用 `{% if %}` 自动开关.

设计意图: **artifact 即"共享内存"(父 R/W, 子 R), subagent 内 messages 即"私有内存"**.
不引入新 store, 不依赖 prompt 自律, 共享/私有边界由代码强约束.
