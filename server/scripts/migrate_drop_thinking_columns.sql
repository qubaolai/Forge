-- ============================================================
-- 删除 models 表废弃字段:
--   - thinking_type
--   - thinking_default
--
-- 使用方法:
--   mysql -h HOST -u USER -p DATABASE < migrate_drop_thinking_columns.sql
-- ============================================================

ALTER TABLE models
  DROP COLUMN thinking_type,
  DROP COLUMN thinking_default;
