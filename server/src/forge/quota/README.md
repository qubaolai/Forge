# `forge.quota` — 用户用量额度

按用户维度的滚动窗口 LLM 用量额度（与成本预算 `observability/cost` 互补：预算是「按天金额」，额度是「滚动窗口次数/用量」）。

## 设计理念

1. **滚动窗口**：5 小时 / 7 天两档滚动窗口，贴近真实「短期突发 + 长期总量」双约束。
2. **与成本联动**：`CostTracker.hydrate_baseline` 启动时联动 quota baseline，超限抛 `UserQuotaExceeded`。
3. **进程内累计 + 可演进**：先在进程内累计，后续接共享存储即可平替多进程。

## 模块速览

```
quota/
└── usage.py   ← UsageQuotaManager + UsageQuotaStatus / WindowStatus + UserQuotaExceeded + UsageQuotaEvent
```

## 如何使用

```python
from forge.quota import get_usage_quota_manager
status = await get_usage_quota_manager().status(user_id)   # 查看滚动窗口用量
await get_usage_quota_manager().hydrate_baseline()         # 启动期装载
# 超限时内部抛 UserQuotaExceeded
```

`GET /v1/chat/quota` 即透出 `status()`。

## 如何扩展

- **加窗口档位 / 改阈值**：在 `UsageQuotaManager` 调整窗口定义与限额来源（配置）。
- **多进程一致性**：把进程内累计替换为 Redis 滚动计数。

## 边界与注意

- 与 `LLMBudgetExceeded`（金额预算）是两套独立约束，分别在不同维度拦截。
