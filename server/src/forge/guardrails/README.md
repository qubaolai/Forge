# `forge.guardrails` — 护栏

工具执行与 LLM 调用的安全/合规检查。

## 模块速览

```
guardrails/
├── tool/
│   └── rate_limiter.py    ← 【已接线】ToolExecutor 调用, per-tool 滑动窗口限速
└── compliance/
    └── audit_logger.py    ← 【已接线】LLM 调用审计 (llm/dispatch/auditor) + 危险工具留痕
```

## 设计理念

1. **检查器即可组合单元**：每个护栏是一个独立检查器，按需挂进执行流水线，关注点单一。
2. **危险操作留痕**：`tool.dangerous=True` 的工具调用经 `compliance/audit_logger` 写 `audit.jsonl`（当前注册工具均为只读/低风险，机制保留）。

> 历史说明：原 access_filter / permission_check / dangerous_op_blocker 与 input/output 护栏目录
> 是为多 agent 角色与 CLI 工具集设计的，已随服务端瘦身（仅保留 Web Chat 路径）删除。
> 未来需要新增护栏时，写独立检查器并在 `ToolExecutor` / LLM 网关中间件挂载即可。
