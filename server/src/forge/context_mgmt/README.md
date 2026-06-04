# `forge.context_mgmt` — 统一上下文管理系统

把「给 LLM 喂什么」从一坨拼接逻辑收敛成一条可观测、可降级、可扩展的流水线。chat 主链通过 `ContextManager` 调用本系统。

## 设计理念

1. **统一入口 + Fork-Join 并行构建**：`ContextManager.build()` 内部 `BudgetPolicy.allocate` → 并行（`PromptRenderer.render` + `ContentGatherer.gather`）→ `MessageAssembler.assemble` → 输出 `ContextSnapshot`（带用量与降级信息）。
2. **软降级**：`ContentGatherer` 用 `asyncio.gather(return_exceptions=True)`——summary/facts 失败进 `degraded`（不致命），只有 history 失败才硬抛。
3. **压缩与构建解耦**：`CompactionController`（Trigger + Strategy 两层解耦）可独立触发，构建流程不感知压缩细节。
4. **值对象统一**：`types.py` 定义 `ContextRequest` / `WindowBudget` / `ContextSnapshot` / `ContextUsage` / `CompactionResult`，所有扩展点围绕它们交互。
5. **三种模式同一套机制**：chat / task / workflow 通过扩展点（filter / tool_policy / provider）切换，而非分叉代码。

## 模块速览

```
context_mgmt/
├── manager.py        ← ContextManager: 对外唯一入口 (build + 压缩编排)
├── protocols.py      ← 所有扩展点 ABC (ContentProvider/HistoryFilter/ToolResultPolicy/BudgetPolicy/TokenMeter)
├── types.py          ← 统一值对象
├── builder/          ← 构建流水线 (factory / prompt_renderer / content_gatherer / message_assembler)
├── budget/           ← BudgetPolicy: token 预算分配
├── filters/          ← HistoryFilter: Hybrid(近期锚点+语义) / Null / StepScoped
├── tool_policy/      ← ToolResultPolicy: Verbatim / Truncating / Evicting / Summarizing
├── providers/        ← ContentProvider: history / summary / facts / workspace / workflow_step
├── compaction/       ← CompactionController + Trigger(threshold/explicit) + Strategy(summary/null)
├── digest/           ← 会话内容引用化 (大消息读时折叠 + 异步 digest 任务)
├── recall/           ← 语义历史召回 (embedding 打分, opt-in)
├── meter/            ← TokenMeter: token 计量
└── memory_factory.py ← MemoryStore 装配 (接 SummaryStore)
```

## 如何使用

```python
from forge.context_mgmt.manager import ContextManager
from forge.context_mgmt.types import ContextRequest, ContextMode

snapshot = await context_manager.build(
    ContextRequest(user_id=..., session_id=..., mode=ContextMode.CHAT, ...),
    allow_compaction=True,
    on_compaction_started=..., on_compaction_done=...,   # SSE 钩子
)
snapshot.messages       # 喂给 LLM 的消息列表
snapshot.usage          # 分层 token 用量 (前端可渲染)
snapshot.degraded       # 软降级原因列表
```

工厂按 mode 装配扩展点：`builder/factory.py` 的 `build_context_builder(mode=...)`。

## 如何扩展

- **新内容源**：实现 `ContentProvider`，在 factory 注入。
- **新历史过滤策略**：实现 `HistoryFilter`（如基于时间窗 / 重要性）。
- **新工具结果策略**：实现 `ToolResultPolicy`（跨轮 tool 结果如何保留：原样/截断/占位/摘要）。
- **新预算策略 / token 计量**：实现 `BudgetPolicy` / `TokenMeter`。
- **新压缩触发/策略**：实现 `compaction/trigger` 或 `compaction/strategy`。

> ⚠️ 当前生产只有 CHAT 模式经过本系统；TASK / WORKFLOW 分支（EvictingPolicy / SummarizingPolicy / StepScopedFilter / workflow_step provider）是**为 CLI / workflow 预留**的扩展点，已就绪但尚未接入生产路径——保留勿删。

## 边界与注意

- digest 默认开启（`context.digest.enabled=true`）；semantic_recall 默认 opt-in（有额外延迟/成本）。
- chat 默认语义仍是 `HybridFilter + TruncatingPolicy`，零退化。
- 压缩失败不终止主流程，回退到压缩前上下文并标 `degraded=compaction_failed`。
