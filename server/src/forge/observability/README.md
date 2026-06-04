# `forge.observability` — 可观测性

四条链路：日志、追踪、指标、成本/预算。目标是「单机能跑、生产能接 OTel/Prometheus/Langfuse」的软依赖设计。

## 设计理念

1. **软依赖**：Prometheus / OTel / Langfuse 都是可选 exporter，不装也能跑（降级为 none）。
2. **自动上下文注入**：日志自动带 `trace_id/user_id/client_type`，无需每行手动传。
3. **埋点即上下文管理器**：`with span("name") as s: s.set(...)`，agent 内核 stream/step/llm_call/tool 四级埋点，chat prepare/assemble/compact/finalize 均埋点。
4. **成本可治理**：`CostTracker` 进程内累计 + 周期 flush 到 `cost.jsonl`，配合 `BudgetConfig` 三级预算与 `quota` 滚动窗口，超限抛 `LLMBudgetExceeded`。

## 模块速览

```
observability/
├── logging/      ← setup_logging (文本/JSON 双格式 + 上下文注入)
├── tracing/      ← span() + exporter 切换 (none/otel/langfuse)
├── metrics/      ← LLM 指标 (请求量/延迟/token/成本/熔断/预算)
├── cost/         ← CostTracker + flush_cost 任务 (周期落 cost.jsonl)
├── alerting/     ← 告警
└── replay/       ← 事件回放
```

## 如何使用

```python
from forge.observability.tracing.tracer import span
with span("chat.prepare", user_id=uid) as s:
    s.set("session_id", sid)
    ...   # 异常会被 span 记录

from forge.observability.logging.logger import setup_logging
setup_logging()   # 启动期调一次
```

## 如何扩展

- **加 exporter**：在 `tracing/` 实现新 exporter 并在配置切换。
- **加指标**：在 `metrics/` 注册新指标（注意 Prometheus 软依赖，缺库时降级）。
- **加埋点**：在热路径用 `span()` 包裹，`s.set()` 标注关键属性。

## 边界与注意

- `business/technical/cost_metrics` 部分仍为占位。
- 成本 flush 只写 `cost.jsonl`（文件），不入关系库；`CostTracker.flush_to_db()` 与 `hydrate_baseline()` 现已不吃 session_factory。
