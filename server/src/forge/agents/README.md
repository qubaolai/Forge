# `forge.agents` — Agent 内核与扩展机制

> 这是理解整个后端的钥匙。一句话：**单一 `ReActAgent` 内核 + 可组合 `AgentLifecycle` 扩展**，所有 mode 差异（chat / plan_exec / workflow）都被外置为 lifecycle 组合，绝不在内核里写 `if mode == ...`。

## 设计理念

1. **内核唯一**：只有一个 `ReActAgent`（`react/agent.py`）承载全部智能体执行。经典 ReAct（Reason+Act），用 LLM 原生 function calling，不做文本解析。
2. **扩展点唯一**：所有定制都通过 `AgentLifecycle` 的 8 个 hook 注入，绝不改内核。`lifecycle=None` ⇒ 退化为纯 ReAct。
3. **mode = lifecycle 组合**：编排层按 `agent_profile` 装配不同 lifecycle 列表，这就是「全部 mode 路由逻辑」。新增 mode 不需要改 Python（加 YAML profile + prompt 模板即可）。
4. **隔离优于自律**：Plan Mode 物理不暴露写工具 schema（LLM 看不到也调不到），而不是靠 prompt 约束。
5. **配置即治理**：`agent_profiles` 启动期 7 项强校验，不过则拒绝启动（fail-fast）。

## 模块速览

```
agents/
├── react/agent.py            ← ReActAgent 内核 (stream() 主入口)
├── lifecycle.py              ← AgentLifecycle ABC(8 hook) + MultiLifecycle 组合器 + NoopLifecycle
├── base.py                   ← BaseAgent / AgentResult / AgentEvent 公共类型
├── plan_mode.py              ← PlanModeLifecycle (Claude Code 风格 Plan-Exec, 动态工具集)
├── workflow_lifecycle.py     ← WorkflowLifecycle (模板驱动 phase 流水线 + gate)
├── persistence_lifecycle.py  ← RunStorePersistenceLifecycle (CLI 持久化投影)
├── hitl.py                   ← DecisionRegistry + PendingDecision (人机交互阻塞/唤醒)
├── profiles.py               ← load_profiles_at_startup (启动期 7 项强校验)
├── run_orchestrator.py       ← RunOrchestrator (CLI 路径编排: 装配 lifecycle + 跑 agent)
├── run_supervisor.py         ← RunSupervisor (CLI run 进程内注册表 + abort)
└── roles/                    ← AgentRole + 7 内置角色 + 子 agent 工厂
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

# 带扩展 (chat / CLI 路径由编排层装配)
agent = ReActAgent(llm, tools=tools, system_prompt=prompt,
                   lifecycle=MultiLifecycle([guard, plan_mode, persistence]))
```

mode 的实际 lifecycle 组合：

| mode | lifecycle 组合 | 装配点 |
|------|----------------|--------|
| chat | `[GuardLifecycleAdapter]` | `chat/runner.py` |
| plan_exec | `[Guards, PlanModeLifecycle, RunStorePersistenceLifecycle]` | `run_orchestrator._build_lifecycles` |
| workflow | `[Guards, WorkflowLifecycle, RunStorePersistenceLifecycle]` | 同上 |

## 如何扩展

- **加一个新行为**：写一个 `AgentLifecycle` 子类（继承 `NoopLifecycle`，只覆写需要的 hook），在编排层 append 进 `MultiLifecycle`。
- **加一个新 mode**：在 `config/sys_config.*.yaml` 的 `agent_profiles` 加一段 profile（工具白名单 / 模型档位 / Plan 开关 / 持久化目标）+ 写 prompt 模板。如需特殊行为再写一个 lifecycle。
- **加一个子 agent 角色**：`roles/factory.py` 的 `register_custom_agent_role`，声明 `allowed_tools` / `can_write` / `write_path_prefixes`。

### MultiLifecycle 的三种合并策略（写自定义组合时必须知道）

| hook | 合并策略 |
|------|----------|
| `resolve_tools` / `before_step` / `before_tool_call` | **首个非 None 胜出**（避免互相覆盖） |
| `on_tool_result` | **pipeline 累计**（依次替换，形成管道） |
| `on_start` / `after_step` / `on_complete` / `on_error` | **全部都调** + 单 lifecycle 异常隔离 |

## 边界与注意

- lifecycle 的持久化失败**不阻断**主流程（best-effort 投影）。
- HITL：lifecycle 在 `before_tool_call` 创建 `PendingDecision` 并 `await event.wait()` 阻塞；外部 `POST /v1/decisions/{token}` 调 `DecisionRegistry.resolve` 唤醒。
- 子 agent 受 `AgentRole.can_write` + profile `sub_agents_allowed` 双重约束，工具层 hard mask 写类工具。
