# `forge.agents` — Agent 内核与扩展机制

> 这是理解整个后端的钥匙。一句话：**单一 `ReActAgent` 内核 + 可组合 `AgentLifecycle` 扩展**，所有 mode 差异都被外置为 lifecycle 组合，绝不在内核里写 `if mode == ...`。

## 设计理念

1. **内核唯一**：只有一个 `ReActAgent`（`react/agent.py`）承载全部智能体执行。经典 ReAct（Reason+Act），用 LLM 原生 function calling，不做文本解析。
2. **扩展点唯一**：所有定制都通过 `AgentLifecycle` 的 8 个 hook 注入，绝不改内核。`lifecycle=None` ⇒ 退化为纯 ReAct。
3. **mode = lifecycle 组合**：编排层按 `agent_profile` 装配不同 lifecycle 列表，这就是「全部 mode 路由逻辑」。新增 mode 不需要改 Python（加 YAML profile + prompt 模板即可）。
4. **配置即治理**：`agent_profiles` 启动期 5 项强校验，不过则拒绝启动（fail-fast）。
5. **内核依赖干净**：仅依赖 `forge.core.types` / `forge.llm.contracts` / `forge.tools` / `forge.prompts` / `forge.observability`，零 FastAPI/DB 依赖——未来 CLI 胖客户端可以共享包形式直接复用本内核，在客户端本地装配自己的 lifecycle 组合（Plan/Workflow/HITL 等都是客户端本地行为）。

> 历史说明：旧 CLI 执行路径的 `plan_mode.py` / `workflow_lifecycle.py` / `persistence_lifecycle.py` / `hitl.py` / `run_orchestrator.py` / `run_supervisor.py` 已随服务端瘦身整体删除（服务端只保留 Web Chat 路径，CLI 未来直连 `/v1/llm/chat/completions`）。

## 模块速览

```
agents/
├── react/agent.py   ← ReActAgent 内核 (stream() 主入口)
├── lifecycle.py     ← AgentLifecycle ABC(8 hook) + MultiLifecycle 组合器 + NoopLifecycle
├── base.py          ← BaseAgent / AgentResult / AgentEvent 公共类型
├── profiles.py      ← load_profiles_at_startup (启动期 5 项强校验)
└── roles/           ← AgentRole + 7 内置角色 + 子 agent 工厂
```

## 单步执行序列（所有 hook 的挂载坐标系）

```
on_start  →  for step:  resolve_tools → before_step → [LLM 流式]
                        → for tc: before_tool_call → 执行/veto → on_tool_result
                        → after_step
          →  on_complete / on_error
```

- 同 step 内连续 `parallelism_safe` 工具并行（`asyncio.gather`），unsafe 串行。
- `abort_event` 多点检查实现优雅中断。

## 如何使用

```python
# 纯 ReAct (无扩展)
agent = ReActAgent(llm, tools=tools, system_prompt=prompt, max_steps=20)
async for event in agent.stream(user_input):
    ...

# 带扩展 (chat 路径由编排层装配)
agent = ReActAgent(llm, tools=tools, system_prompt=prompt,
                   lifecycle=MultiLifecycle([guard]))
```

mode 的实际 lifecycle 组合：

| mode | lifecycle 组合 | 装配点 |
|------|----------------|--------|
| chat | `[GuardLifecycleAdapter]` | `chat/runner.py` |

## 如何扩展

- **加一个新行为**：写一个 `AgentLifecycle` 子类（继承 `NoopLifecycle`，只覆写需要的 hook），在编排层 append 进 `MultiLifecycle`。
- **加一个新 mode**：在 `config/sys_config.*.yaml` 的 `agent_profiles` 加一段 profile（工具白名单 / 模型档位 / 持久化目标）+ 写 prompt 模板。如需特殊行为再写一个 lifecycle。
- **加一个子 agent 角色**：`roles/factory.py` 的 `register_custom_agent_role`，声明 `allowed_tools` / `can_write` / `write_path_prefixes`。

### MultiLifecycle 的三种合并策略（写自定义组合时必须知道）

| hook | 合并策略 |
|------|----------|
| `resolve_tools` / `before_step` / `before_tool_call` | **首个非 None 胜出**（避免互相覆盖） |
| `on_tool_result` | **pipeline 累计**（依次替换，形成管道） |
| `on_start` / `after_step` / `on_complete` / `on_error` | **全部都调** + 单 lifecycle 异常隔离 |

## 边界与注意

- lifecycle 的扩展行为失败应 best-effort 隔离，**不阻断**主流程。
- 子 agent 受 `AgentRole.can_write` + profile `sub_agents_allowed` 双重约束，spawn 层 `SUBAGENT_DENY_TOOLS` hard mask 写类/二级派发工具。
