# `forge.guardrails` — 护栏

分层的安全/合规检查。当前**真正接线**的是工具护栏与合规审计；输入/输出护栏目录是预留框架骨架。

## 设计理念

1. **检查器即可组合单元**：每个护栏是一个独立检查器，按需挂进流水线，关注点单一。
2. **工具执行的护栏流水线**：`ToolExecutor` 串起 access→permission→rate_limit→dangerous_op，工具本身不写安全逻辑。
3. **危险操作留痕**：危险工具调用经 `compliance/audit_logger` 写 `audit.jsonl`。
4. **分层占位、按需落地**：input/output/compliance 目录预留了一套护栏框架结构，按业务需要逐步实现（避免提前堆砌）。

## 模块速览

```
guardrails/
├── tool/                      ← 【已接线】ToolExecutor 调用
│   ├── access_filter.py       ← 工具可达性
│   ├── permission_check.py    ← 权限/作用域
│   ├── rate_limiter.py        ← 频率限制
│   └── dangerous_op_blocker.py← 危险操作拦截
├── compliance/
│   ├── audit_logger.py        ← 【已接线】危险调用审计 (llm/dispatch/auditor 调用)
│   └── disclaimer_injector.py ← (占位)
├── input/                     ← (占位框架) toxicity / jailbreak / pii / prompt_injection / topic
├── output/                    ← (占位框架) grounding / hallucination / moderation / pii_leakage / format
└── pipeline.py                ← (占位) 护栏流水线编排
```

## 如何使用

工具护栏由 `ToolExecutor` 自动调用，业务无需手动触发：

```python
# tools/executor.py 内部
from forge.guardrails.tool.dangerous_op_blocker import DangerousOpBlocker
from forge.guardrails.tool.permission_check import PermissionChecker
# access → permission → rate_limit → dangerous_op
```

## 如何扩展

- **加工具护栏**：在 `tool/` 写检查器，挂进 `ToolExecutor` 流水线。
- **落地输入/输出护栏**：在 `input/` `output/` 的占位文件实现具体检查器，并在 `pipeline.py` 编排接入 LLM 调用前后。

## 边界与注意

- input/output/compliance.disclaimer 当前是 1 行占位文件，属未实现框架，**不是死代码**，按需落地。
- 真正的运行时安全边界是「工具白名单 + Plan Mode 动态工具集 + 路径策略 + 工具护栏 + LoopGuard」多层叠加。
