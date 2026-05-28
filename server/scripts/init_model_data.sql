-- ============================================================
-- Forge 模型配置初始化 SQL
-- 使用方法: mysql -h host -u user -p database < init_model_data.sql
-- 表结构由服务启动时 bootstrap_schema 自动创建，本脚本只写数据。
-- ============================================================

-- ------------------------------------------------------------
-- 1. 供应商 (providers)
-- snowflake id 由应用层生成；手动插入时用固定值。
-- ------------------------------------------------------------
INSERT INTO providers (id, provider_id, name, impl, base_url, is_enabled, priority, routing_config, created_at, updated_at)
VALUES
(1001, 'prov_dashscope', 'dashscope', NULL, NULL, 1, 10,
  '{"fallback_chain":[{"provider":"openai","model":"gpt-4o-mini"}],"max_retries":3,"retry_backoff_seconds":1.0}',
  NOW(), NOW()),
(1003, 'prov_deepseek',  'deepseek',  NULL, NULL, 1, 30, '{}', NOW(), NOW())
ON DUPLICATE KEY UPDATE name=VALUES(name);

-- ------------------------------------------------------------
-- 2. API Key (provider_keys)
-- ★ key_ciphertext 需要填入真实的 AES 加密后的 API Key
--    先用占位符 'CHANGE_ME_xxxxxxxxx'，上线前替换为真实加密值。
--    AES 加密方法见 server/src/forge/utils/crypto.py
-- ------------------------------------------------------------
INSERT INTO provider_keys (id, key_id, provider_id, key_ciphertext, key_fingerprint, is_enabled, weight, created_at, updated_at)
VALUES
(2001, 'pkey_ds_1',   1001, 'CHANGE_ME_DASHSCOPE_KEY',  'sk-xxx-ds',  1, 1, NOW(), NOW()),
(2002, 'pkey_openai', 1002, 'CHANGE_ME_OPENAI_KEY',    'sk-xxx-oai', 1, 1, NOW(), NOW()),
(2003, 'pkey_ds2',    1003, 'CHANGE_ME_DEEPSEEK_KEY',  'sk-xxx-dp',  1, 1, NOW(), NOW()),
(2004, 'pkey_anth',   1004, 'CHANGE_ME_ANTHROPIC_KEY', 'sk-xxx-ant', 1, 1, NOW(), NOW())
ON DUPLICATE KEY UPDATE key_id=VALUES(key_id);

-- ------------------------------------------------------------
-- 3. 模型 (models) — 文本模型 (model_type = 'text')
-- ------------------------------------------------------------
INSERT INTO models (id, model_id, provider_id, name, display_name, model_type,
  context_window, max_output_tokens,
  supports_tools, supports_images, supports_thinking,
  thinking_options,
  extra_params, cost_tier, is_enabled, is_default, priority, created_at, updated_at)
VALUES
-- DashScope / 通义千问
(3001, 'mdl_ds_max',    1001, 'qwen3-max-preview', '通义千问 Max', 'text',
  32768,  8192,  true, false, false, NULL,
  '{"temperature":0.7}', 'mid',   true, false, 90, NOW(), NOW()),
(3002, 'mdl_ds_plus',   1001, 'qwen-plus', '通义千问 Plus', 'text',
  131072, 8192,  true, false, false, NULL,
  '{"temperature":0.7}', 'cheap', true, true,  100, NOW(), NOW()),
(3003, 'mdl_ds_36plus', 1001, 'qwen3.6-plus', '通义千问 qwen3.6-plus', 'text',
  131072, 8192,  true, false, false, NULL,
  '{"temperature":0.7}', 'mid',   true, false, 80, NOW(), NOW()),
(3004, 'mdl_ds_flash',  1001, 'qwen3.5-flash', '通义千问 qwen3.5-flash', 'text',
  8192,   4096,  true, false, false, NULL,
  '{"temperature":0.7}', 'cheap', true, false, 70, NOW(), NOW()),

-- DeepSeek
(3021, 'mdl_dp_pro',  1003, 'deepseek-v4-pro',  'DeepSeek V4 PRO',  'text',
  1000000, 32768, true, false, true,
  '["standard","low","medium","high","xhigh"]',
  '{"temperature":0.7}', 'expensive', true, true,  100, NOW(), NOW()),
(3022, 'mdl_dp_flash', 1003, 'deepseek-v4-flash', 'DeepSeek V4 flash', 'text',
  1000000, 32768, true, false, true,
  '["standard","low","medium","high","xhigh"]',
  '{"temperature":0.7}', 'mid',       true, false, 90, NOW(), NOW())
ON DUPLICATE KEY UPDATE name=VALUES(name);

-- ------------------------------------------------------------
-- 4. 模型 (models) — Embedding 模型 (model_type = 'embedding')
-- ------------------------------------------------------------
INSERT INTO models (id, model_id, provider_id, name, display_name, model_type,
  context_window, max_output_tokens,
  supports_tools, supports_images, supports_thinking,
  extra_params, cost_tier, is_enabled, is_default, priority, created_at, updated_at)
VALUES
(3101, 'mdl_emb_ds', 1001, 'text-embedding-v3', '通义千问 Embedding V3', 'embedding',
  0, 0, false, false, false,
  '{"dimension":1024,"batch_size":10,"max_retries":3,"retry_backoff":1.0}',
  'cheap', true, true, 100, NOW(), NOW())
ON DUPLICATE KEY UPDATE name=VALUES(name);

-- ------------------------------------------------------------
-- 5. 模型 (models) — Reranker 模型 (model_type = 'reranker')
-- ------------------------------------------------------------
INSERT INTO models (id, model_id, provider_id, name, display_name, model_type,
  context_window, max_output_tokens,
  supports_tools, supports_images, supports_thinking,
  extra_params, cost_tier, is_enabled, is_default, priority, created_at, updated_at)
VALUES
(3201, 'mdl_rerank_ds', 1001, 'gte-rerank', '通义千问 GTE Rerank', 'reranker',
  0, 0, false, false, false,
  '{"timeout":5.0,"truncation":{"strategy":"tail","max_doc_chars":4000,"monitor_threshold":0.1}}',
  'cheap', true, true, 100, NOW(), NOW())
ON DUPLICATE KEY UPDATE name=VALUES(name);
