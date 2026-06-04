# 接入本地 Ollama 供应商（chat + 向量）

本文说明如何把通过 [Ollama](https://ollama.com) 在本地部署的小模型（chat）和向量模型（embedding）接入 Forge。

Forge 的供应商体系是「动态注册 + 数据库记录」设计，没有 provider 类型硬枚举，因此本地供应商已是一等公民：

- **chat**：`OllamaLLM`（`server/src/forge/llm/providers/ollama.py`），继承 `OpenAICompatibleLLM`，走 Ollama 的 OpenAI 兼容端点 `/v1/chat/completions`。
- **向量**：`OllamaEmbedder`（`server/src/forge/retrieval/embedders/ollama_embedder.py`），复用 `openai` SDK 走 `/v1/embeddings`。

两者都已通过 `@register_llm("ollama")` / `@register_embedder("ollama")` 注册，`impl` 名统一为 **`ollama`**。

---

## 0. 关于占位 API Key（务必先读）

Ollama 本地服务**默认无鉴权**，但 Forge 的 LLM 客户端池与向量解析都以 API Key 为索引：

- LLM 池预热会跳过空 key（`api/lifespan.py`），无 key 的 provider 拿不到调用候选；
- 向量解析对非 `mock` 的 impl 强制要求 key（`retrieval/bound_model_resolver.py`）。

所以接入时需要给 ollama 供应商注册**一个占位 key**（任意非空字符串，例如 `ollama`）。Ollama 会忽略它，纯粹是为了满足「以 key 为索引」的约束。

> base_url 必须是 Ollama 的 **OpenAI 兼容根路径** `http://localhost:11434/v1`（openai SDK 会在其后自动拼 `/chat/completions`、`/embeddings`）。不要填原生路径 `/api/embed`，否则会 404。

---

## 1. 准备 Ollama

```bash
ollama serve                     # 启动服务, 默认监听 11434
ollama pull qwen2.5:0.5b         # 一个支持 tool calling 的小 chat 模型
ollama pull nomic-embed-text     # 向量模型, 维度 768
```

> 不同向量模型维度不同（如 `nomic-embed-text`=768、`bge-m3`=1024）。后续配置里的 `dimension` 必须与模型实际维度一致，否则首次向量化会 fail-fast 报错。

---

## 2. 在数据库创建 provider 行

provider 记录由使用方自行写入（后端未提供创建 provider 的管理 API）。示例 SQL：

```sql
INSERT INTO providers (name, impl, base_url, is_enabled, priority)
VALUES ('ollama', 'ollama', 'http://localhost:11434/v1', 1, 2);
```

- `impl` 必须是 `ollama`（对应注册的实现类）。
- `base_url` 是 **chat** 调用的地址；远程 Ollama 改成对应主机即可。NULL 时落到代码默认 `http://localhost:11434/v1`。

> 注意：`providers.base_url` 只作用于 chat。**向量**的 base_url 不走这里，而是写在 embedding 模型配置的 `provider_options.base_url`（见第 5 步），因为系统绑定解析只透传模型的 `provider_options`。

---

## 3. 注册占位 Key

```bash
curl -X POST http://localhost:8553/api/v1/providers/ollama/keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "ollama", "weight": 1}'
```

---

## 4. 新增 chat 模型

```bash
curl -X POST http://localhost:8553/api/v1/providers/ollama/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "qwen2.5:0.5b",
    "display_name": "Qwen2.5 0.5B (本地)",
    "model_type": "chat",
    "cost_tier": "cheap",
    "priority": 0,
    "config": {
      "context_window": 32768,
      "max_output_tokens": 4096,
      "input_modalities": ["text"],
      "output_modalities": ["text"],
      "capabilities": ["tools"],
      "provider_options": {"temperature": 0.7}
    }
  }'
```

- `capabilities`：模型支持 tool calling 就填 `["tools"]`，否则填 `[]`（很多小模型不支持工具调用）。
- 模型可调用性受能力声明约束；能力填错会导致工具调用异常。

---

## 5. 新增向量模型

```bash
curl -X POST http://localhost:8553/api/v1/providers/ollama/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "nomic-embed-text",
    "display_name": "Nomic Embed Text (本地)",
    "model_type": "embedding",
    "cost_tier": "cheap",
    "config": {
      "dimension": 768,
      "supported_dimensions": [768],
      "batch_size": 16,
      "max_batch_size": 32,
      "input_modalities": ["text"],
      "max_retries": 3,
      "retry_backoff": 1.0,
      "provider_options": {"base_url": "http://localhost:11434/v1"}
    }
  }'
```

- `provider_options.base_url` 是**向量调用的地址**（必须显式写，远程 Ollama 改这里）。不填则落到代码默认 `http://localhost:11434/v1`。
- `dimension` 必须等于模型实际维度；`dimension` 创建后不可改，换维度需新增模型再切绑定。

记下返回里的模型 `id`（`model_id`），下一步绑定要用。

---

## 6. 绑定为 RAG 向量模型

```bash
curl -X PUT http://localhost:8553/api/v1/admin/model-bindings/rag_embedding \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "<上一步返回的 model_id>"}'
```

切换或首次绑定向量模型后，已有知识库的向量索引会变为 `stale`，需要重建。

---

## 7. 触发向量重建

```bash
curl -X POST http://localhost:8553/api/v1/admin/rag-index/rebuild \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# 查询进度
curl http://localhost:8553/api/v1/admin/rag-index/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

---

## 8. 验证

- **chat**：在 `/api/v1/chat/completions` 选用 ollama 的 chat 模型，确认正常流式返回。
- **向量 / RAG**：上传知识库文档 → ingest 经 `OllamaEmbedder` 向量化 → `knowledge_search` 能召回。
- **注册冒烟**（无需 Ollama 运行）：

  ```bash
  cd server
  python -c "from forge.llm.registry import list_providers; from forge.retrieval.embedders.factory import EmbedderFactory; print('ollama' in list_providers(), 'ollama' in EmbedderFactory.list_providers())"
  # 预期: True True
  ```
