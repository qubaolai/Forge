-- ============================================================================
-- providers + provider_keys 初始化数据
--
-- API Key 通过环境变量获取，加密后写入 provider_keys 表。
-- 首次部署时手工执行此 SQL 插入供应商，然后通过管理 API 添加 Key。
--
-- 执行: mysql -h HOST -u USER -p DATABASE < init_providers.sql
-- ============================================================================

INSERT INTO providers (name, impl, base_url, is_enabled, priority) VALUES
('anthropic',  'anthropic', NULL, 1, 10),
('deepseek',   'deepseek',  NULL, 1,  8),
('dashscope',  'dashscope', NULL, 1,  5),
('openai',     'openai',    NULL, 1,  3)
ON DUPLICATE KEY UPDATE name=name;
