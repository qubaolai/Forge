-- ============================================================
-- Forge 模型配置初始化 SQL
-- 使用方法: mysql -h host -u user -p database < init_model_data.sql
-- 表结构由服务启动时 bootstrap_schema 自动创建，本脚本只写示例数据。
-- 所有 id 均为示例雪花 ID；生产环境建议通过管理 API 或 seed_models.py 创建。
-- 系统绑定保持为空，升级后由管理员在页面手动选择。
-- ============================================================

-- 1. 供应商
INSERT INTO providers
  (id, name, impl, base_url, is_enabled, priority, routing_config, created_at, updated_at)
VALUES
  (1001, 'dashscope', 'dashscope', NULL, 1, 10, '{}', NOW(), NOW()),
  (1003, 'deepseek', 'deepseek', NULL, 1, 30, '{}', NOW(), NOW())
ON DUPLICATE KEY UPDATE
  impl=VALUES(impl),
  base_url=VALUES(base_url),
  is_enabled=VALUES(is_enabled),
  priority=VALUES(priority),
  routing_config=VALUES(routing_config);

-- 2. 统一模型注册表
-- 兼容物理列仍需提供值，但新业务代码不再读取 context_window、
-- max_output_tokens、supports_*、thinking_options、extra_params、is_default。
INSERT INTO models
  (id, provider_id, name, display_name, model_type,
   context_window, max_output_tokens, supports_tools, supports_images,
   supports_thinking, thinking_options, extra_params,
   cost_tier, is_enabled, is_default, priority, is_stale, created_at, updated_at)
VALUES
  (3001, 1001, 'qwen-plus', '通义千问 Plus', 'chat',
   0, 0, 0, 0, 0, NULL, NULL, 'cheap', 1, 0, 100, 0, NOW(), NOW()),
  (3002, 1003, 'deepseek-v4-pro', 'DeepSeek V4 PRO', 'chat',
   0, 0, 0, 0, 0, NULL, NULL, 'expensive', 1, 0, 100, 0, NOW(), NOW()),
  (3101, 1001, 'text-embedding-v3', '通义千问 Embedding V3', 'embedding',
   0, 0, 0, 0, 0, NULL, NULL, 'cheap', 1, 0, 100, 0, NOW(), NOW()),
  (3201, 1001, 'gte-rerank', '通义千问 GTE Rerank', 'reranker',
   0, 0, 0, 0, 0, NULL, NULL, 'cheap', 1, 0, 100, 0, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  display_name=VALUES(display_name),
  cost_tier=VALUES(cost_tier),
  is_enabled=VALUES(is_enabled),
  priority=VALUES(priority),
  is_stale=VALUES(is_stale);

-- 3. Chat 模型配置
INSERT INTO chat_model_configs
  (id, model_id, context_window, max_output_tokens, input_modalities,
   output_modalities, capabilities, thinking_options, provider_options,
   created_at, updated_at)
VALUES
  (4001, 3001, 131072, 8192, '["text"]', '["text"]', '["tools"]', NULL,
   '{"temperature":0.7}', NOW(), NOW()),
  (4002, 3002, 1000000, 32768, '["text"]', '["text"]', '["tools","thinking"]',
   '["standard","low","medium","high","xhigh"]',
   '{"temperature":0.7}', NOW(), NOW())
ON DUPLICATE KEY UPDATE
  context_window=VALUES(context_window),
  max_output_tokens=VALUES(max_output_tokens),
  input_modalities=VALUES(input_modalities),
  output_modalities=VALUES(output_modalities),
  capabilities=VALUES(capabilities),
  thinking_options=VALUES(thinking_options),
  provider_options=VALUES(provider_options);

-- 4. Embedding 模型配置
INSERT INTO embedding_model_configs
  (id, model_id, dimension, batch_size, input_modalities, max_retries,
   retry_backoff, provider_options, created_at, updated_at)
VALUES
  (4101, 3101, 1024, 10, '["text"]', 3, 1.0, '{}', NOW(), NOW())
ON DUPLICATE KEY UPDATE
  batch_size=VALUES(batch_size),
  input_modalities=VALUES(input_modalities),
  max_retries=VALUES(max_retries),
  retry_backoff=VALUES(retry_backoff),
  provider_options=VALUES(provider_options);

-- 5. Reranker 模型配置
INSERT INTO reranker_model_configs
  (id, model_id, timeout_seconds, max_retries, retry_backoff,
   truncation_strategy, max_doc_chars, monitor_threshold, provider_options,
   created_at, updated_at)
VALUES
  (4201, 3201, 5.0, 2, 1.0, 'tail', 4000, 0.1, '{}', NOW(), NOW())
ON DUPLICATE KEY UPDATE
  timeout_seconds=VALUES(timeout_seconds),
  max_retries=VALUES(max_retries),
  retry_backoff=VALUES(retry_backoff),
  truncation_strategy=VALUES(truncation_strategy),
  max_doc_chars=VALUES(max_doc_chars),
  monitor_threshold=VALUES(monitor_threshold),
  provider_options=VALUES(provider_options);

-- 6. 系统模型绑定：仅创建角色，不自动选择模型
INSERT INTO system_model_bindings
  (id, role, model_id, version, updated_by, created_at, updated_at)
VALUES
  (4301, 'rag_embedding', NULL, 0, NULL, NOW(), NOW()),
  (4302, 'semantic_history_embedding', NULL, 0, NULL, NOW(), NOW()),
  (4303, 'rag_reranker', NULL, 0, NULL, NOW(), NOW())
ON DUPLICATE KEY UPDATE role=VALUES(role);
