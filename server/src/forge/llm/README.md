# `agent_platform.llm` — LLM 接入与容错层

本包负责所有跟具体大模型 SDK 的对接,对上层(`agents`, `api`)只暴露一个统一的 LLM 接口。

## 模块速览

```
llm/
├── providers/              ← 具体厂商实现
│   ├── base.py             LLM 抽象基类 (chat / chat_stream / chat_with_tools[_stream])
│   ├── openai.py           OpenAICompatibleLLM + OpenAILLM / DeepSeekLLM / DashScopeCompatLLM
│   ├── anthropic.py        AnthropicLLM
│   ├── google.py           GoogleLLM (Gemini)
│   └── mock.py             单元测试用 MockLLM / MockStreamLLM
├── gateway.py              注册表 + 工厂 (@register_llm + LLMFactory + build_chain_from_settings)
├── fallback.py             LLMFallbackChain — retry + provider 切换 + 成本记账
├── cost_tracker.py         按 provider:model 维度的 token 计数
├── retry.py                指数退避重试 (区分可重试/不可重试错误)
├── token_counter.py        prompt token 估算
├── streaming.py            (备用) 流式辅助
├── caching/                (扩展点) 响应缓存
└── router/                 (扩展点) 多模型路由
```

## 总体设计

```
┌────────────────────────────────────────────────────────────────────┐
│ 调用方 (例: ReActAgent)                                             │
│ 只依赖 LLM 接口的 4 个方法:                                          │
│   chat / chat_stream / chat_with_tools / chat_with_tools_stream    │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ 看到的就是一个 LLM 对象
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ LLMFallbackChain  (fallback.py)                                    │
│  - 包装 [primary, *fallbacks] 一组 LLM                              │
│  - 加 retry (call_with_retry, 指数退避)                              │
│  - 加 cost_tracker 记账 (每次成功/失败都打点)                        │
│  - 流式: 拿到首包前可切下一个 provider, 首包后锁定 (语义一致性)        │
│  - 自身也是 LLM 接口 → 对上层透明                                    │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ 链中每个元素都是一个具体 LLM
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ LLMGateway  (gateway.py)                                           │
│  - @register_llm("xxx") 类装饰器, 类被 import 时自注册到 _REGISTRY  │
│  - LLMFactory.create(impl, config) → 按注册名实例化具体 LLM         │
│  - build_chain_from_settings(settings) →                           │
│       LLMConfig.resolve() 选 provider/model →                       │
│       create primary + create fallbacks → LLMFallbackChain         │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ 装好的 LLM 实例
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ 具体 Provider (providers/)                                          │
│  - 每个厂商一个类, 继承 LLM (或 OpenAICompatibleLLM)                 │
│  - __init__(config: dict) 解析自己关心的字段                         │
│  - 真正的 SDK 调用 + 响应解析                                        │
└────────────────────────────────────────────────────────────────────┘
```

### 为什么 Gateway 和 FallbackChain 拆两层

- **Gateway** 解决「**哪些 provider 存在,怎么造**」——纯注册 + 工厂,只跟启动时的 yaml 配置打交道。
- **FallbackChain** 解决「**调用时挂了怎么办**」——纯运行时容错策略,与具体是什么 provider 无关。

合并成一层会让两个完全不同的演化方向耦合在一起:换 retry 策略 → 动注册表;接新 provider → 懂 retry 逻辑。拆开后,新增 provider 只需要写一个 `@register_llm("xxx") class XxxLLM(LLM)`,什么都不用动。

### Provider 类层级

```
LLM (ABC, base.py)
├── OpenAICompatibleLLM         共享 OpenAI Chat Completions API 的实现
│   ├── OpenAILLM               api.openai.com
│   ├── DeepSeekLLM             api.deepseek.com (覆盖 _build_kwargs / _messages_payload
│   │                           / chat_with_tools_stream 以支持 thinking 模式)
│   └── DashScopeCompatLLM      dashscope.aliyuncs.com/compatible-mode/v1
├── AnthropicLLM                api.anthropic.com (走 messages.create)
├── GoogleLLM                   Gemini (走 generative-ai-python)
├── MockLLM                     测试用 echo
└── MockStreamLLM               测试用 真流式 echo
```

子类继承策略遵循 **「不破坏父类行为,只覆盖差异方法」**。例如 DeepSeekLLM 跟 OpenAI 的差异只在 thinking 模式,就只 override 三个方法,其他 chat / chat_stream / chat_with_tools 走父类。

## 配置入口

```yaml
# config/sys_config.yaml
llm:
  provider: dashscope               # 默认 provider
  default_model: qwen-plus          # 默认 model
  max_retries: 3
  retry_backoff_seconds: 1.0

  providers:
    deepseek:
      models:
        - name: deepseek-v4-pro
          display_name: DeepSeek V4 PRO
          context_window: 1000000
          thinking: true              # 推理类开关 1: 启用思考模式
          reasoning_effort: medium    # 推理类开关 2: 思考深度
      default_params:
        temperature: 0.7
        timeout: 30
```

### 推理类参数 (thinking / reasoning_effort)

只两个开关。各 provider 自行翻译,不支持的字段静默忽略:

| 字段 | DeepSeek | OpenAI o-series | Anthropic | Google |
|---|---|---|---|---|
| `thinking: true` | `extra_body={thinking:{type:enabled}}` | (忽略) | `thinking={type:enabled,budget:5000}` 顶层 | (忽略) |
| `reasoning_effort` | 顶层 kwarg | 顶层 kwarg | (忽略) | (忽略) |

前端通过 `model_options` 按对话覆盖:

```ts
// frontend → POST /chat/completions
{
  "message": "...",
  "model_options": {
    "thinking": true,
    "reasoning_effort": "high"
  }
}
```

`extra_options` 优先级 > yaml 静态配置。

## 配置流转(从 yaml 到 SDK 调用)

```
sys_config.yaml
   │  pydantic 解析
   ▼
config.settings.LLMConfig
   │  LLMConfig.resolve(provider, model) → (impl_name, model_name, call_config)
   │     call_config 合并 default_params + model fields, 但 api_key/model 必胜
   ▼
LLMFactory.create(impl, call_config)
   │  按 @register_llm 表查类, 实例化
   ▼
DeepSeekLLM.__init__(call_config)
   │  从 dict 抽自己关心的字段: api_key/model/base_url/temperature/
   │   max_tokens/top_p/reasoning_effort/thinking
   ▼
LLMFallbackChain([primary, *fallbacks])
   │  对上层伪装成一个 LLM
   ▼
ReActAgent.stream → chain.chat_with_tools_stream(messages, tools, extra_options)
   ▼
DeepSeekLLM.chat_with_tools_stream
   │  _build_kwargs 合并 yaml + 调用时 extra_options
   │  _messages_payload 给 assistant 消息回灌 reasoning_content
   ▼
self._client.chat.completions.create(...)
```

## 新增一个 Provider

1. 在 `providers/yourname.py` 写实现:
   ```python
   from ..gateway import register_llm
   from .base import LLM, ChatResult, ChatMessage

   @register_llm("yourname")
   class YourLLM(LLM):
       def __init__(self, config: dict):
           super().__init__(config)
           # 解析 api_key / model / 其他字段

       @property
       def model_name(self) -> str: ...
       @property
       def provider_name(self) -> str: ...

       def chat(self, messages, *, temperature=None, max_tokens=None, extra_options=None):
           ...  # 你的 SDK 调用
   ```
2. 在 `gateway._autoload` 的 `mod_name` 元组里加 `"yourname"`。
3. yaml 里加 provider 配置块,api_key 用 `${ENV_VAR:}` 占位。
4. 跑一遍 `LLMFactory.list_providers()` 确认能看到新名字。

## FallbackChain 语义细节

| 场景 | 行为 |
|---|---|
| `chat / chat_with_tools` (非流式) | 每个 provider 内部 retry,全部 retry 失败再切下一个 provider |
| `chat_stream / chat_with_tools_stream` (流式) | **首包前可切**,首包后报错直接抛 — 因为前端已经看到一半内容,中途切 provider 会拼接出不一致的输出 |
| `supports_tool_calling == False` 的 provider | 工具相关方法自动跳过(`chat_with_tools` 系列),非工具方法照走 |
| cost_tracker | 成功失败都打点,粒度 = `provider:model`;失败计入 `errors` 字段 |

流式 fallback 的「首包前」边界由 `next(stream)` 触发——拿到第一个 chunk 之前的任何异常(连接错误、API 4xx、首包前的 StopIteration)都会切下一个 provider。

## 日志与追踪

每次对话从 HTTP 进入到 LLM 返回,主路径上有以下日志(都带 `trace_id`):

| 标签 | 位置 | 级别 |
|---|---|---|
| `turn.start` / `turn.done` | `api/routes/v1/chat.py` | INFO |
| `llm.select` | `llm/gateway.py:build_chain_from_settings` | INFO |
| `ctx.built` | `api/routes/v1/chat.py` (`context.builder.CompositeContextBuilder.build` 后) | INFO |
| `react.step` | `agents/react/agent.py` 每个 step 末尾 | INFO |
| `tool.exec` | `tools/executor.py:execute` 末尾 | INFO |
| `llm.request` | `providers/openai.py` (`_log_llm_request` helper) | DEBUG |

对单次请求的全量排查,把 `app.log_level` 调到 `DEBUG`,然后用 `trace_id` 过滤即可拉出完整链路。

## 测试

- `tests/unit/test_llm_fallback.py` — Fallback chain 语义(retry / fallback / 流式锁定 / cost_tracker)
- `tests/unit/test_react_agent.py` — Agent + 工具调用的端到端流式行为(用 `_ScriptedLLM` 替代真 SDK)

写新 provider 时建议同步加一个集成测试(可选 `pytest.mark.live` 跳过,只在本地/CI 跑)。
