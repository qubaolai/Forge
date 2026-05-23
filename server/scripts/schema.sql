-- ============================================================================
-- Forge DDL — MySQL 8.0+
--
-- 规范:
--   1. 所有 id 为 BIGINT Snowflake (应用层生成, 应用层 Snowflake 赋值覆盖自增)
--   2. 业务 ID (xxx_id VARCHAR(40) UNIQUE) 对外暴露
--   3. 每表强制 created_at + updated_at
--   4. 无 FOREIGN KEY 约束, 引用列仅 BIGINT + INDEX
--   5. utf8mb4 + InnoDB
--
-- 执行: mysql -h HOST -u USER -p DATABASE < schema.sql
-- ============================================================================

-- ============================================================================
-- 1. users — 用户账号
-- ============================================================================
DROP TABLE IF EXISTS users;
CREATE TABLE users (
    id            BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    user_id       VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: user_xxx',
    email         VARCHAR(255) NOT NULL UNIQUE COMMENT '登录邮箱',
    name          VARCHAR(64)  NOT NULL COMMENT '显示名称',
    avatar_url    VARCHAR(512) NULL COMMENT '头像 URL',
    role          VARCHAR(16)  NOT NULL DEFAULT 'member' COMMENT 'owner/admin/member/guest',
    status        VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active/disabled/pending',
    password_hash TEXT         NOT NULL COMMENT 'bcrypt 哈希密码',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户账号表';

-- ============================================================================
-- 2. user_api_keys — API Key 认证
-- ============================================================================
DROP TABLE IF EXISTS user_api_keys;
CREATE TABLE user_api_keys (
    id           BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    apikey_id    VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: apikey_xxx',
    user_id      BIGINT       NOT NULL COMMENT '→ users.id (应用层引用)',
    name         VARCHAR(128) NOT NULL COMMENT 'Key 名称标签',
    key_hash     VARCHAR(64)  NOT NULL UNIQUE COMMENT 'API Key 的 SHA256 哈希',
    prefix       VARCHAR(12)  NOT NULL COMMENT 'Key 前缀, 用于 UI 回显',
    last_used_at DATETIME     NULL COMMENT '最后使用时间',
    expires_at   DATETIME     NULL COMMENT '过期时间, NULL=永不过期',
    is_revoked   TINYINT      NOT NULL DEFAULT 0 COMMENT '是否已吊销',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_apikey_hash (key_hash),
    INDEX ix_apikey_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户 API Key 表';

-- ============================================================================
-- 3. chat_sessions — 聊天会话
-- ============================================================================
DROP TABLE IF EXISTS chat_sessions;
CREATE TABLE chat_sessions (
    id           BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    session_id   VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: sess_xxx',
    user_id      VARCHAR(40)  NOT NULL COMMENT '用户业务ID (user_xxx)',
    title        VARCHAR(255) NOT NULL DEFAULT '' COMMENT '会话标题',
    workspace_id VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'workspace 路径',
    status       VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT 'active/deleted',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_cs_user (user_id),
    INDEX ix_cs_status (status),
    INDEX ix_cs_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='聊天会话表';

-- ============================================================================
-- 4. chat_messages — 聊天消息
-- ============================================================================
DROP TABLE IF EXISTS chat_messages;
CREATE TABLE chat_messages (
    id                    BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    message_id            VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: msg_xxx',
    session_id            BIGINT       NOT NULL COMMENT '→ chat_sessions.id',
    role                  VARCHAR(16)  NOT NULL COMMENT 'user/assistant/system',
    content               MEDIUMTEXT   NOT NULL COMMENT '消息文本',
    status                VARCHAR(16)  NOT NULL DEFAULT 'done' COMMENT 'pending/streaming/done/error/partial/aborted',
    parent_id             BIGINT       NULL COMMENT '→ chat_messages.id',
    tool_calls            JSON         NULL COMMENT '工具调用记录',
    citations             JSON         NULL COMMENT '引用列表',
    `usage`                 JSON         NULL COMMENT 'token 用量',
    error_message         TEXT         NULL COMMENT '错误信息',
    reasoning_content     MEDIUMTEXT   NULL COMMENT '思考链文本 (DeepSeek thinking)',
    reasoning_duration_ms INT          NULL COMMENT '思考耗时 ms',
    context_meta          JSON         NULL COMMENT '上下文元信息',
    created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_cm_session (session_id),
    INDEX ix_cm_parent (parent_id),
    INDEX ix_cm_role_status (role, status),
    INDEX ix_cm_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='聊天消息表';

-- ============================================================================
-- 5. session_summaries — 会话摘要
-- ============================================================================
DROP TABLE IF EXISTS session_summaries;
CREATE TABLE session_summaries (
    id                        BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    session_id                VARCHAR(40)  NOT NULL UNIQUE COMMENT '会话业务ID (sess_xxx)',
    workspace_id              VARCHAR(255) NULL COMMENT '工作空间 ID',
    content                   TEXT         NOT NULL COMMENT '摘要正文',
    covered_until_message_id  VARCHAR(40)  NULL COMMENT '摘要覆盖到的最后一条消息',
    token_count               INT          NOT NULL DEFAULT 0 COMMENT '摘要 token 估算',
    version                   INT          NOT NULL DEFAULT 1 COMMENT '版本号, 每次重生成 +1',
    created_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_summary_workspace (workspace_id),
    INDEX ix_summary_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话摘要表';

-- ============================================================================
-- 6. knowledge_bases — 知识库元数据
-- ============================================================================
DROP TABLE IF EXISTS knowledge_bases;
CREATE TABLE knowledge_bases (
    id              BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    kb_id           VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: kb_xxx',
    name            VARCHAR(128) NOT NULL COMMENT '知识库名称',
    description     TEXT         NULL COMMENT '描述',
    visibility      VARCHAR(16)  NOT NULL DEFAULT 'private' COMMENT 'private/workspace/public',
    owner_id        BIGINT       NOT NULL COMMENT '→ users.id (应用层引用)',
    collaborators   JSON         NULL COMMENT '协作者列表 [{user_id, user_name, permission}]',
    embedding_model VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'embedding 模型标识',
    chunk_size      INT          NOT NULL DEFAULT 512 COMMENT '分块字符数',
    chunk_overlap   INT          NOT NULL DEFAULT 64 COMMENT '分块重叠字符数',
    document_count  INT          NOT NULL DEFAULT 0 COMMENT '文档总数 (denormalized)',
    chunk_count     INT          NOT NULL DEFAULT 0 COMMENT '分块总数 (denormalized)',
    size_bytes      BIGINT       NOT NULL DEFAULT 0 COMMENT '文件总字节数 (denormalized)',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_kb_owner (owner_id),
    INDEX ix_kb_visibility (visibility)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库元数据表';

-- ============================================================================
-- 7. kb_documents — KB 文档
-- ============================================================================
DROP TABLE IF EXISTS kb_documents;
CREATE TABLE kb_documents (
    id             BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    doc_id         VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: doc_xxx',
    kb_id          BIGINT       NOT NULL COMMENT '→ knowledge_bases.id (应用层引用)',
    name           VARCHAR(512) NOT NULL COMMENT '显示文件名',
    source         VARCHAR(16)  NOT NULL DEFAULT 'upload' COMMENT 'upload/url/api',
    source_url     VARCHAR(2048) NULL COMMENT '原始 URL',
    storage_path   VARCHAR(1024) NULL COMMENT '文件存储路径/对象存储 key',
    mime_type      VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream' COMMENT 'MIME 类型',
    size_bytes     BIGINT       NOT NULL DEFAULT 0 COMMENT '文件字节大小',
    content_hash   VARCHAR(128) NULL COMMENT 'SHA256 去重',
    status         VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/parsing/chunking/embedding/indexed/failed',
    status_message TEXT         NULL COMMENT '状态详情/失败原因',
    progress       INT          NOT NULL DEFAULT 0 COMMENT '处理进度 0-100',
    chunk_count    INT          NOT NULL DEFAULT 0 COMMENT '切分后的分块数',
    indexed_at     DATETIME     NULL COMMENT '完成索引时间',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_kb_docs_kb_created (kb_id, created_at),
    INDEX ix_kb_docs_status (kb_id, status),
    INDEX ix_kb_docs_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文档表';

-- ============================================================================
-- 8. kb_document_chunks — KB 文档父块
-- ============================================================================
DROP TABLE IF EXISTS kb_document_chunks;
CREATE TABLE kb_document_chunks (
    id          BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    chunk_id    VARCHAR(64)  NOT NULL UNIQUE COMMENT '业务ID: chunk_xxx',
    document_id BIGINT       NOT NULL COMMENT '→ kb_documents.id (应用层引用)',
    kb_id       BIGINT       NOT NULL COMMENT '→ knowledge_bases.id (denormalized)',
    seq         INT          NOT NULL DEFAULT 0 COMMENT '分块顺序',
    content     TEXT         NOT NULL COMMENT '父块原文文本',
    header_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '标题路径',
    chunk_hash  VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '文本哈希去重',
    source_type VARCHAR(32)  NOT NULL DEFAULT 'text' COMMENT 'pdf/docx/md/html/text',
    extra       JSON         NULL COMMENT '元数据: page/section/表格原文 URL',
    token_count INT          NOT NULL DEFAULT 0 COMMENT '估算 token 数',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_chunks_doc_seq (document_id, seq),
    INDEX ix_chunks_kb (kb_id),
    INDEX ix_chunks_hash (chunk_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文档父块表';

-- ============================================================================
-- 9. providers — LLM 供应商
-- ============================================================================
DROP TABLE IF EXISTS providers;
CREATE TABLE providers (
    id             BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    provider_id    VARCHAR(40) NOT NULL UNIQUE COMMENT '业务ID: prov_xxx',
    name           VARCHAR(64)  NOT NULL COMMENT 'anthropic/openai/deepseek/dashscope',
    impl           VARCHAR(64)  NOT NULL COMMENT 'SDK 实现类名',
    base_url       VARCHAR(512) NULL COMMENT 'API 地址, NULL=官方默认',
    is_enabled     TINYINT      NOT NULL DEFAULT 1,
    priority       INT          NOT NULL DEFAULT 0,
    routing_config JSON         NULL COMMENT 'fallback 图谱 / 重试策略',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX ix_prov_name (name),
    INDEX ix_prov_enabled (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LLM 供应商表';

-- ============================================================================
-- 10. provider_keys — 供应商 API Key
-- ============================================================================
DROP TABLE IF EXISTS provider_keys;
CREATE TABLE provider_keys (
    id              BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    key_id          VARCHAR(40)  NOT NULL UNIQUE COMMENT '业务ID: pkey_xxx',
    provider_id     BIGINT       NOT NULL COMMENT '→ providers.id',
    key_ciphertext  TEXT         NOT NULL COMMENT 'API Key (AES-256-GCM 加密)',
    key_fingerprint VARCHAR(12)  NOT NULL COMMENT 'Key 指纹, 日志脱敏',
    is_enabled      TINYINT      NOT NULL DEFAULT 1,
    weight          INT          NOT NULL DEFAULT 1 COMMENT '负载权重',
    cooldown_until  DATETIME     NULL COMMENT '429 冷却到何时',
    failure_score   INT          NOT NULL DEFAULT 0,
    last_error_at   DATETIME     NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX ix_pk_provider (provider_id),
    INDEX ix_pk_enabled (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商 API Key 表';

-- ============================================================================
-- 11. refresh_token_blacklist — Token 黑名单
-- ============================================================================
DROP TABLE IF EXISTS refresh_token_blacklist;
CREATE TABLE refresh_token_blacklist (
    id         BIGINT      AUTO_INCREMENT PRIMARY KEY COMMENT 'Snowflake ID',
    jti        VARCHAR(64) NOT NULL UNIQUE COMMENT 'JWT ID, refresh token 唯一标识',
    user_id    BIGINT      NOT NULL COMMENT '→ users.id (应用层引用)',
    expires_at DATETIME    NOT NULL COMMENT '原 token 过期时间, 用于定期清理',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX ix_blacklist_user (user_id),
    INDEX ix_blacklist_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已撤销的 refresh token 黑名单';
