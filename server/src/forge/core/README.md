# `forge.core` — 跨层公共原语

被几乎所有模块依赖的底座：领域类型、异常体系、统一响应、请求上下文、加解密。**只被依赖，不依赖业务模块**（避免循环依赖）。

## 设计理念

1. **零业务、零循环依赖**：core 是依赖图的叶子，任何模块都能安全 import 它。
2. **协议对齐**：`types/message.py` 与 OpenAI Chat Completions 对齐，便于跨 provider 复用。
3. **统一契约**：异常（`exceptions.py`）、响应（`response.py`）、请求上下文（`request_context.py` 的 ContextVar）三者给全栈一致的边界语义。

## 模块速览

```
core/
├── types/
│   ├── message.py        ← Message / Role / ToolCall (OpenAI 兼容)
│   ├── chunk.py          ← Chunk (RAG 切片)
│   ├── element.py        ← 文档解析元素
│   └── errors.py         ← ToolValidationError 等领域错误
├── exceptions.py         ← 业务异常体系 (NotFound / ...)
├── response.py           ← success() 统一成功响应
├── request_context.py    ← trace_id / user_id / client_type 的 ContextVar
├── content_merge.py      ← strip_overlap 等续写内容合并
├── crypto.py             ← resolve_key 等密钥处理
└── security.py           ← 安全工具
```

## 如何使用

```python
from forge.core.response import success
from forge.core.exceptions import NotFound
from forge.core.request_context import set_trace_id, set_user_id
from forge.core.types.message import Message, Role, ToolCall
```

## 如何扩展

- **加领域类型**：放 `types/`，保持与外部协议（OpenAI）对齐。
- **加异常**：继承 `exceptions.py` 既有基类，确保被 ErrorHandler 中间件正确映射。

## 边界与注意

- 严禁在 core 里 import 业务模块（chat / agents / llm 等），否则破坏依赖方向。
- `request_context` 的 ContextVar 在 chat 背景 task / CLI run 中需显式 set（执行与请求线程解耦）。
