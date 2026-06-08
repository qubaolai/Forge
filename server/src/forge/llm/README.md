# `forge.llm` — LLM 网关(接入 / 选链 / 容错)

本包负责所有与具体大模型 SDK 的对接,对上层(`agents` / `chat` / `memory` / `api`)只暴露**一个统一网关 `LLMGateway`**。业务层不感知 provider / 路由 / 熔断 / fallback。

> 配套:`llm/gateway.py`(入口)、`llm/dispatch/`(选链 + 执行)、`llm/pipeline/`(横切中间件)。

## 模块速览

```
llm/
├── gateway.py              LLMGateway — 业务层唯一入口 (Pre → dispatch → Post)
├── request.py             LLMRequest / LLMResponse / CostEstimate 值对象
├── binding.py             GatewayBinding + GatewayLLMAdapter (代理风格调用方的桥)
├── contracts.py           ToolCallingLLM ABC (ReActAgent 只依赖这个)
├── pipeline/              Pre/Post 中间件 (validator/rate_limit/budget/dedup/cache + cache_write/dedup_complete/audit)
├── dispatch/
│   ├── chain_resolver.py  resolve_chain — 选链层 (user_pin > 档位链 > 系统保底)
│   ├── chain_builder.py   build_dispatch_chain — 把有序 (provider,model) 链展开成 LLMDispatcher
│   ├── dispatcher.py      LLMDispatcher — 单层扁平链遍历 + 熔断 + 重试 + fallback
│   └── auditor.py         per-entry 审计埋点
├── model_config_cache.py  ModelConfigCache — Redis 缓存 provider/key/model + 调用链 (get_chain)
├── client_pool.py         LLMClientPool — 供应商级 key 池 (WRR + 429 冷却), 与模型无关
├── registry.py            @register_llm 注册表 + build_llm_client + _autoload()
├── resilience/            retry (区分可重试/不可重试) + circuit_breaker + bulkhead
├── cost_tracker.py        按 provider:model 维度的 token 计数 + 预算控制
├── token_counter.py       prompt token 估算
├── caching/               精确缓存 / 语义缓存 / 原生 prompt 缓存后端
└── providers/             具体厂商实现 (openai / anthropic / google / ollama / mock)
```

> ⚠️ 历史变更:旧 `router/`(CostAware/LatencyAware/AB 路由)已删除——成本/延迟路由属过度工程化,模型档位已表达该意图;选模型统一收敛到 `chain_resolver`。旧 `LLMFallbackChain` 已更名 `LLMDispatcher`。

## 总体架构

```
LLMRequest
  → Pre  pipeline   (validator → rate_limit → budget → dedup → cache)   ← 可短路(缓存/幂等命中)
  → dispatch        (resolve_chain → build_dispatch_chain → LLMDispatcher 遍历)
  → Provider        (openai / anthropic / google / ollama …)
  → Post pipeline   (cache_write → dedup_complete → audit)
  → LLMResponse
```

业务层两种调用姿势,**最终都进同一套 Pre/Post pipeline**:

- **一次性 utility**(摘要/标题/digest):直接 `get_llm_gateway().complete(LLMRequest(model_profile="fast", ...))`。
- **Agent 风格**(ReActAgent / 子 agent):`GatewayBinding` → `GatewayLLMAdapter`(实现 `ToolCallingLLM`),Agent 只见 `chat / chat_stream / chat_with_tools[_stream]` 四个方法,不感知 gateway。

## 模型选择(选链):`user_pin > 档位链 > 系统保底`

选链逻辑统一在 `dispatch/chain_resolver.py:resolve_chain`,产出**有序 `[(provider, model), ...]`**:

| 优先级 | 触发 | 用哪条链 | 跨厂商 |
|---|---|---|---|
| 1. user_pin | `preferred_provider + preferred_model` 双值(web 对话) | **对话链**[provider]:`[pin] + 同 provider 其余项` | ❌ 绝不跨厂商(保证人类可见输出质量一致) |
| 2. 档位 | `model_profile`(fast/smart/strong;CLI / utility) | **档位链**[tier],保配置顺序 | ✅ 允许(重点是处理任务) |
| 3. 保底 | 都没有 / 上面解析为空 | `settings.llm` 默认模型 | — |

产出后统一过滤:**能力硬约束**(`requires_tools/vision/thinking` + `context_window`)+ **运行时启用态**。`user_pin` 命中后仍过能力校验——pin 了不支持 tools 的模型却发 tool_use 会 **fail-fast** 报清晰错。

> `task_type="utility"` 不再有独立选模型路径:调用方传 `model_profile="fast"` 即走 fast 档位链。

### 两块链区域 + DB 热配

链是 **DB 热配**(可前端拖拽、增删模型即时生效、无需重启),不写死在 yaml:

- **对话链**(`scope="conversation"`,key = provider 名):每个供应商配自己启用模型的有序链;web user_pin 的同供应商后备顺序。
- **档位链**(`scope="tier"`,key = fast/smart/strong):可跨供应商;CLI / utility 用。

落库:表 `model_chains`(`scope` + `chain_key` + `entries` JSON + `version`),ORM `infrastructure/database/orm/model_chain_orm.py`。
读取:`ModelConfigCache.reload_all` 一并加载进 Redis,`get_chain(scope, key)` 供 resolver 读;管理端改链(`PUT /admin/model-chains/{scope}/{key}`,保存时校验每条 (provider, model) 存在 + 启用 + 是 chat)→ 触发 cache reload,即时生效。

## Fallback 与弹性执行(单层扁平链)

关键拆分:**「换 key」是供应商级弹性(池的职责),「换模型」才是 fallback**。两者不再混在一条「key×模型」展开的扁平链里。

- **换 key = 供应商级**:`client_pool.py` 的 key 池按 `impl` 管理(WRR 平滑加权轮询 + 429 冷却),与模型无关。
- **熔断器** key = `(impl, api_key)`(供应商级,model 无关)。
- **dispatcher** 遍历有序 `(provider, model)` 链;同一模型的多 key 仅在 429 时切换,模型整体失败才降级到下一个模型。

错误分类(决定「换 key 留在原模型」还是「降级到下一个模型」):

| 事件 | 动作 |
|---|---|
| 429 限流 | 池冷却该 key(供应商级)+ 换同 provider 下一把 key,不换模型 |
| 瞬时网络错误 | 同 key 重试(`call_with_retry`,指数退避) |
| per-key 熔断 OPEN | 跳过该 key |
| key 全部冷却 / 硬错误(模型不存在/4xx/内容违规)/ 空输出 / 首 token 超时 / bulkhead 拒绝 | 降级到链中下一个 (provider, model) |

**流式语义**:首包前可切下一个 provider;一旦 yield 首包就锁定,后续报错直接抛——避免给前端拼接出不一致的输出。`LLMResponse.fallback_position` = 降到链中第几个模型(0 = 主模型)。

## Pre / Post pipeline

新增横切关注点只需写一个 middleware 注册进 `PipelineRunner`,不动 gateway / dispatcher(OCP)。

| 阶段 | 中间件 | 作用 |
|---|---|---|
| Pre | validator | 入参校验,非法 reject |
| Pre | rate_limit | 入站限流 |
| Pre | budget | 预算检查(全局/默认用户/指定用户三级),超额抛 `LLMBudgetExceeded` |
| Pre | dedup | 幂等(`idempotency_key`),命中**短路** |
| Pre | cache | 精确缓存,命中**短路**(需 `temperature=0` + pin 模型 + 无 tools) |
| Post | cache_write | 写精确缓存(空 content 不写) |
| Post | dedup_complete | 标记幂等完成 |
| Post | audit | 请求级摘要日志 + 危险调用审计 |

## Provider 类层级

```
LLM (ABC, providers/base.py)
├── OpenAICompatibleLLM        共享 OpenAI Chat Completions API
│   ├── OpenAILLM              api.openai.com
│   ├── DeepSeekLLM            api.deepseek.com(覆盖 _build_kwargs / _messages_payload /
│   │                          chat_with_tools_stream 以支持 thinking 模式)
│   ├── DashScopeCompatLLM     dashscope.aliyuncs.com/compatible-mode/v1(阿里通义)
│   └── XiaoMiMIMOLLM          api.xiaomimimo.com/v1(小米 MIMO,带 thinking)
├── AnthropicLLM               api.anthropic.com(messages.create)
├── GoogleLLM                  Gemini
├── (ollama.py)                Ollama 本地模型
├── MockLLM                    测试用 echo
└── MockStreamLLM              测试用 真流式 echo
```

子类策略:**不破坏父类行为,只覆盖差异方法**。如 DeepSeek 与 OpenAI 只差 thinking 模式,就只 override 三个方法。

### 推理类参数(thinking / reasoning_effort)

两个开关,各 provider 自行翻译,不支持的字段静默忽略;只从 `extra_options` 读取(前端 per-request 传入,优先级 > yaml):

| 字段 | DeepSeek | OpenAI o-series | Anthropic | 其他 |
|---|---|---|---|---|
| `thinking: true` | `extra_body={thinking:{type:enabled}}` | (忽略) | 顶层 `thinking={type:enabled,budget:N}` | 视实现 |
| `reasoning_effort` | 顶层 kwarg(`high`/`max`) | 顶层 kwarg(`low`/`medium`/`high`) | (忽略) | 视实现 |

强度文本中英文都认(`低/中/高/超高` 与 `low/medium/high/xhigh/max`,见 `providers/openai.py:_normalize_reasoning_level`)。

## 新增一个 Provider

1. 在 `providers/yourname.py` 写实现:
   ```python
   from ..registry import register_llm
   from .base import LLM, ChatChunk

   @register_llm("yourname")
   class YourLLM(LLM):
       @property
       def provider_name(self) -> str: return "yourname"
       def chat_stream(self, messages, *, model, ...): ...   # 子类必实现(非流式默认走它聚合)
   ```
2. 在 `registry._autoload()` 的 `mod_name` 元组里加 `"yourname"`(外部 SDK 缺失会 DEBUG 跳过,不影响其他 provider)。
3. 在管理端建 provider + model + key(落 DB,经 `ModelConfigCache` 生效);无需改 yaml。
4. `registry.list_providers()` 确认能看到新名字。

## 测试

- `tests/unit/llm/test_chain_resolver.py` — 选链:user_pin 同 provider 后备(不跨厂商)/ 档位链跨 provider 保序 / 能力过滤 / fail-fast / 系统保底。
- `tests/unit/llm/test_chain_save_validation.py` — 链保存校验:存在 / 启用 / chat 类型 / 对话链同 provider。
- `tests/unit/test_llm_fallback.py` — dispatcher:retry / fallback / 流式首包锁定 / 供应商级熔断 / cost_tracker。
- `tests/unit/llm/test_gateway_cache_only.py` — build_dispatch_chain 展开 + 池 + spec 映射。
- `tests/e2e/test_llm_chain_e2e.py` — 真实模型端到端(`@pytest.mark.e2e` + `FORGE_E2E=1` 门控,默认跳过):档位链真实调用 + 解析校验。

> 真实 e2e 运行:`FORGE_E2E=1 .venv/bin/python -m pytest tests/e2e -q`(需可用 DB / Redis / 真实 Key 或本地 ollama)。
