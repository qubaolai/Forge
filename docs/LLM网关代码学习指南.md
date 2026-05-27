# Forge LLM 网关代码学习指南（含函数级精读清单）

这份文档用于快速吃透 `server/src/forge/llm/` 的实现。
建议按“主链路 -> 横切能力 -> 路由配置 -> 启动接入 -> 测试验证”的顺序学习。

---

## 1. 学习目标

1. 理解主调用链：`LLMRequest -> LLMGateway -> Pipeline -> Dispatcher -> Provider -> LLMResponse`。
2. 理解三类关键机制：路由选型、容错回退、缓存/限流/预算。
3. 掌握扩展点：新增 Provider、Router 策略、中间件。
4. 能独立排查：超时、fallback、缓存命中、重复请求、配额拦截问题。

---

## 2. 一图看全链路

```mermaid
flowchart LR
A["业务调用方\nchat/adaptive/memory"] --> B["LLMRequest"]
B --> C["LLMGateway.complete/stream"]
C --> D["Pre Pipeline\nvalidator/rate_limit/budget/dedup/cache"]
D --> E["Router\nRule/Cost/Latency"]
E --> F["Dispatch Chain Builder\nprovider+model+keys"]
F --> G["LLMDispatcher\nretry/circuit-breaker/bulkhead/fallback"]
G --> H["Provider Client\nopenai/deepseek/dashscope"]
H --> I["Post Pipeline\ncache_write/dedup_complete/audit"]
I --> J["LLMResponse / Stream Chunks"]
```

---

## 3. 整体学习路径（建议顺序）

## 阶段 A：先打通主链路（必须）

1. `server/src/forge/llm/request.py`
2. `server/src/forge/llm/gateway.py`
3. `server/src/forge/llm/dispatch/chain_builder.py`
4. `server/src/forge/llm/dispatch/dispatcher.py`
5. `server/src/forge/llm/providers/base.py`
6. `server/src/forge/llm/providers/openai.py`
7. `server/src/forge/llm/registry.py`

目标：明确一次请求如何从业务层走到 provider，再回到网关响应。

## 阶段 B：横切能力（稳定性/成本核心）

1. `server/src/forge/llm/pipeline/base.py`
2. `server/src/forge/llm/pipeline/validator.py`
3. `server/src/forge/llm/pipeline/rate_limit.py`
4. `server/src/forge/llm/pipeline/budget.py`
5. `server/src/forge/llm/pipeline/dedup.py`
6. `server/src/forge/llm/pipeline/cache.py`
7. `server/src/forge/llm/pipeline/audit.py`

目标：理解每个中间件在请求前后做什么，以及短路行为。

## 阶段 C：路由与配置

1. `server/src/forge/llm/router/base.py`
2. `server/src/forge/llm/router/composite.py`
3. `server/src/forge/llm/router/rule_based.py`
4. `server/src/forge/llm/router/cost_aware.py`
5. `server/src/forge/llm/router/latency_aware.py`
6. `server/src/forge/config/domains/llm.py`
7. `server/config/sys_config.dev.yaml`

目标：明确 `preferred_*`、router、默认配置的优先级和兜底。

## 阶段 D：启动接入与运行态

1. `server/src/forge/api/lifespan.py`
2. `server/src/forge/chat/orchestrator.py`
3. `server/src/forge/adaptive/task_runner.py`
4. `server/src/forge/memory/summary/service.py`

目标：理解网关如何被初始化、注入并被上层业务调用。

## 阶段 E：测试反推设计

1. `server/tests/unit/test_llm_fallback.py`
2. `server/tests/unit/test_router.py`
3. `server/tests/unit/llm/test_pipeline_phase4.py`
4. `server/tests/unit/llm/test_client_pool.py`
5. `server/tests/unit/llm/test_gateway_cache_only.py`

目标：用测试确认设计边界和失败场景。

---

## 4. 函数级精读清单（按模块）

使用方式：每个函数至少回答 5 个问题。

1. 输入参数有什么约束。
2. 返回值的业务语义是什么。
3. 关键分支条件是什么。
4. 失败时抛什么异常，谁来兜底。
5. 对指标/成本/缓存有什么副作用。

### 4.1 网关入口：`gateway.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `LLMGateway.__init__` | 注入 settings、pipeline、router | 默认 pipeline 组装顺序 | 自定义 pipeline 时漏掉 post middlewares |
| `_default_pipeline` | 构建默认 pre/post 链 | pre 顺序决定短路优先级 | 顺序调整会改变行为 |
| `complete` | 非流式主入口 | pre 短路、dispatcher 调用、post 收口 | response 的 provider/model/fallback 信息是否准确 |
| `complete_with_tools` | 非流式工具调用入口 | `req.tools` 非空约束 | 调用方误用导致 `ValueError` |
| `_complete_with_tools_after_pre` | 完成工具调用核心逻辑 | finish_reason 归一化策略 | tool_calls 与 finish_reason 不一致 |
| `stream` | 流式主入口 | 缓存命中回放、首包前后异常、finally 收口 | 流结束后 post 失败被吞掉（仅日志） |
| `stream_with_tools` | 流式工具调用入口 | chunk 协议字段稳定性 | 前端对 chunk 结构假设过强 |
| `estimate_cost` | 离线成本估算 | token 估算来源、model/provider 解析 | 未传 provider/model 时直接异常 |
| `_resolve_provider_model` | 统一路由决策 | user pin > router > default | model_cache 不可用时回退路径 |
| `_build_available_candidates` | 为 router 准备候选集合 | capabilities 转换容错 | 模型脏数据导致候选缺失 |
| `_build_dispatcher_for` | 构建 dispatcher（chat/utility 两路） | `task_type=utility` 特殊链路 | utility 与普通链混淆 |
| `_dedup_failure_cleanup` | 非流式失败后清理幂等状态 | 仅在异常路径触发 | 忘记调用会造成幂等卡死 |

### 4.2 调度核心：`dispatch/dispatcher.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `LLMDispatcher.__init__` | 装配 chain + resilience 组件 | `_chain` 顺序即 fallback 顺序 | 误以为会自动 provider 级 fallback |
| `_check_entry_can_proceed` | 判断 entry 是否可尝试 | breaker、tool 支持、blocked group | 条件过严导致跳过过多 entry |
| `_record_success` | 成功收口 | 成本记账、breaker success、审计 | fallback 位置信息遗漏 |
| `_record_failure` | 失败收口 | 429 冷却与非 429 分流 | 错误分流不准造成不必要回退 |
| `_mark_key_cooldown_if_rate_limited` | 429 key 冷却 | retry-after 解析 | 冷却时间过短导致抖动 |
| `_on_retry_for_key` | 重试回调 | 429 时提前停止当前 key 重试 | 与全局 retry 策略冲突 |
| `_dispatch_non_stream` | 非流式通用调度 | bulkhead + retry + fallback 主体 | last_exc 处理不当丢失根因 |
| `_dispatch_stream` | 流式通用调度 | “首包前可回退，首包后锁定” | 把流中途异常当可回退 |
| `chat` | 非流式普通调用 | spec 与 runtime 参数合并 | 覆盖顺序错误 |
| `chat_with_tools` | 非流式工具调用 | tool provider 能力检查 | 无支持 provider 时提示不清晰 |
| `chat_stream` | 流式普通调用 | chunk usage/final reason 提取 | usage 丢失影响成本统计 |
| `chat_with_tools_stream` | 流式工具调用 | tool chunk 协议一致性 | tool_calls 聚合不完整 |
| `_stream_first` | 抽象“取首包” | async/sync 双迭代支持 | 空流处理不一致 |
| `_stream_iter` | 抽象“消费后续包” | 统一异步消费形态 | 同步流异常传播细节 |
| `_call_kwargs` | 由 spec 展开调用参数 | None 字段过滤策略 | 模型必填字段丢失 |
| `_extra_options` | 合并 DB 参数与请求参数 | runtime 优先 | 透传字段污染 |

### 4.3 链构建：`dispatch/chain_builder.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `build_dispatch_chain` | 普通任务链构建 | 同 provider/model 的 key 级候选 | 误以为会跨 provider 回退 |
| `build_utility_dispatch_chain` | utility 三段候选构建 | utility -> task_model -> default | 候选去重导致意外顺序 |
| `_build_entries_from_cache` | cache -> clients/specs | ready 检查、enabled 检查、key 列表 | cache 未就绪直接失败 |
| `_utility_candidates` | utility 候选去重 | reason 字段用于日志定位 | 配置为空时候选全空 |

### 4.4 Provider 抽象与实现：`providers/base.py` + `providers/openai.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `LLM.chat` | 非流式默认实现（聚合 stream） | async 迭代聚合方式 | 把 async iterator 当普通 list |
| `LLM.chat_stream` | 流式抽象接口 | 子类必须实现 | 返回类型不符合 chunk 协议 |
| `LLM.chat_with_tools` | 工具调用抽象接口 | 默认 NotImplemented | dispatcher 需判断能力 |
| `LLM.chat_with_tools_stream` | 工具流默认降级实现 | 非流式退化为单块流 | 与真流式行为差异 |
| `OpenAICompatibleLLM._build_kwargs` | 组装 OpenAI 参数 | extra_options 合并、thinking/reasoning | 字段名与 SDK 不一致 |
| `OpenAICompatibleLLM.chat_stream` | OpenAI 流式文本 | delta/usage/final reason 提取 | 空 choices 处理 |
| `OpenAICompatibleLLM.chat_with_tools` | 非流式 tools | tool_calls 结构解析 | JSON 参数解析失败兜底 |
| `OpenAICompatibleLLM.chat_with_tools_stream` | 流式 tools | 增量 tool_calls 缓冲拼接 | 索引错位导致参数串联错误 |
| `DeepSeekLLM._build_kwargs` | deepseek thinking 特化 | extra_body.thinking 注入 | thinking 关闭时字段清理 |
| `DeepSeekLLM._messages_payload` | reasoning_content 回灌 | 多轮思考链兼容 | 忘回灌导致 400 |

### 4.5 Provider 注册工厂：`registry.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `register_llm` | provider 装饰器注册 | 重复注册保护 | provider 名冲突 |
| `build_llm_client` | 工厂创建 client | impl 必须已注册 | 未注册 provider 直接失败 |
| `_autoload` | 启动自动加载 providers | ImportError 分级日志 | provider 文件注释掉导致“配置有，注册无” |
| `split_provider_model` | 解析 `provider:model` | 缺省 provider 兼容 | 非法格式没提前清洗 |

### 4.6 Pipeline：`pipeline/*.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `PipelineRunner.run_pre` | pre 链执行 | 短路返回 LLMResponse | 中间件返回约定不统一 |
| `PipelineRunner.run_post` | post 链执行 | 后处理顺序和幂等 | post 异常吞掉影响观测 |
| `InputValidatorMiddleware.process` | 输入校验 | 空消息/参数合法性 | 校验规则与上层 schema 重复冲突 |
| `InboundRateLimitMiddleware.process` | 入站限流 | user 维度和窗口算法 | 匿名请求 user_id 归属 |
| `BudgetMiddleware.process` | 预算检查 | user/day/global 限制优先级 | 阈值配置空值处理 |
| `DeduplicationMiddleware.process` | 幂等去重 | in-flight/已完成语义 | 崩溃后状态恢复 |
| `ExactCacheMiddleware.process` | 精确缓存读 | cache key 组成和温度约束 | 温度非 0 误缓存 |
| `CacheWriteMiddleware.process` | 写回缓存 | 写缓存条件 | 异常结果误入缓存 |
| `DedupCompleteMiddleware.process` | 幂等完成收口 | success/failure 状态切换 | 遗漏失败清理 |
| `AuditMiddleware.process` | 网关聚合审计 | 业务字段与 dispatch 审计互补 | 双重打点口径不一致 |

### 4.7 路由：`router/*.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `CompositeRouter.route` | 按顺序执行策略 | 第一命中即返回 | 策略顺序改变行为 |
| `RuleBasedRouter.route` | 显式规则路由 | requires_tools/vision/thinking 等 | 规则缺失回退不透明 |
| `CostAwareRouter.route` | 成本优先路由 | 价格表依赖与估算 | 未命中价格时退化策略 |
| `LatencyAwareRouter.route` | 延迟优先路由 | 历史指标依赖 | 冷启动时无样本 |
| `ABTestRouter.route` | 实验流量切分 | 用户一致性哈希 | 实验比例漂移 |

### 4.8 运行态资源：`client_pool.py` / `model_config_cache.py`

| 函数 | 作用 | 必看点 | 常见坑 |
|---|---|---|---|
| `LLMClientPool.reconcile_provider` | 同步 key 集合与实例池 | 增删 key 与复用策略 | key 抖动导致频繁建连 |
| `LLMClientPool.get_candidates_by_impl` | 获取可用候选 | 冷却 key 过滤 | 全部冷却时空候选处理 |
| `ModelConfigCache.is_ready` | 配置可用性门闸 | 启动阶段行为 | 未就绪时请求风暴 |
| `ModelConfigCache.get_models/get_keys` | 模型与 key 读取 | enabled 过滤 | 读缓存陈旧导致误判 |

---

## 5. 精读时推荐加的断点与日志

1. 在 `gateway.complete`、`gateway.stream` 入参处打断点，看 `LLMRequest` 全字段来源。
2. 在 `gateway._resolve_provider_model` 查看 `reason`，确认是否命中 user pin。
3. 在 `chain_builder._build_entries_from_cache` 检查最终 entries 数量与 key 顺序。
4. 在 `dispatcher._dispatch_non_stream` 和 `_dispatch_stream` 观察 `idx` 跳转轨迹。
5. 在 `dispatcher._record_failure` 观察 `was_rate_limited` 分流。
6. 在 `pipeline` 的 pre/post 起止处记录 `idempotency_key` 与 `cache_hit`。

---

## 6. 实操打卡路线（3 天）

## Day 1：读懂主链路

1. 读完阶段 A 全部文件。
2. 手动画 1 张时序图（从 `LLMRequest` 到 `LLMResponse`）。
3. 回答：首包前 fallback 与首包后锁定的边界是什么。

## Day 2：掌握横切能力

1. 读完阶段 B 文件。
2. 跑 `test_pipeline_phase4.py`、`test_gateway_cache_only.py`。
3. 回答：缓存命中时哪些中间件还会执行。

## Day 3：能改能扩展

1. 读完阶段 C + D。
2. 跑 `test_llm_fallback.py`、`test_router.py`。
3. 选一个小扩展：新增一条路由规则或一个 middleware 字段透传。

---

## 7. 常见排障问题速查

1. 明明配置了 provider 但报“未注册的 LLM provider”。
2. 请求偶发超时但没 fallback。
3. 流式场景只返回部分内容。
4. 成本统计与实际 token 不对齐。
5. 同一个请求被重复扣费。

排查顺序建议：

1. 看 `registry._autoload` 是否加载成功。
2. 看 `model_config_cache` 是否 ready。
3. 看 `dispatcher` 审计里每个 idx 的 error/finish_reason。
4. 看 `pipeline` 里 dedup/cache/budget 的命中路径。
5. 最后看 provider 实现细节（chunk 解析、usage 提取）。

---

## 8. 阅读完成标准（自测）

满足以下 8 条可视为“掌握网关代码”：

1. 能口述完整调用链与每层职责。
2. 能解释非流式与流式的 fallback 差异。
3. 能解释 429 与非 429 为什么走不同路径。
4. 能说明 `task_type=utility` 的特殊性。
5. 能定位配置问题在“注册/缓存/路由/调度/provider”哪一层。
6. 能新增一个中间件并接入 pre 或 post 链。
7. 能新增一个 provider 并完成注册到可调用。
8. 能补一条单测验证你改动的边界行为。

