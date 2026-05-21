/**
 * Mock 数据库:用 localStorage 持久化,刷新页面后数据保留。
 * 仅用于前端开发,不要在生产模式启用 MSW。
 */

import {
  Agent, AuditLog, ChatMessage, ChatSession, KnowledgeBase,
  KnowledgeDocument, DocumentChunk, ModelEndpoint, ToolDefinition, User,
} from '@/types';

const STORAGE_KEY = 'rag-mock-db';

interface MockDB {
  users: User[];
  passwords: Record<string, string>;       // email -> password(明文,仅 mock)
  knowledgeBases: KnowledgeBase[];
  documents: KnowledgeDocument[];
  chunks: DocumentChunk[];
  agents: Agent[];
  sessions: ChatSession[];
  messages: ChatMessage[];
  models: ModelEndpoint[];
  tools: ToolDefinition[];
  auditLogs: AuditLog[];
}

function uid(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function now(): string {
  return new Date().toISOString();
}

// ---- 默认种子数据 ----
function seed(): MockDB {
  const owner: User = {
    id: 'user_owner',
    email: 'owner@example.com',
    name: '管理员',
    role: 'owner',
    status: 'active',
    created_at: now(),
    updated_at: now(),
  };
  const member: User = {
    id: 'user_member',
    email: 'member@example.com',
    name: '小明',
    role: 'member',
    status: 'active',
    created_at: now(),
    updated_at: now(),
  };

  const kb: KnowledgeBase = {
    id: 'kb_demo',
    name: '产品手册',
    description: '产品使用手册和发布说明',
    visibility: 'workspace',
    owner_id: owner.id,
    collaborators: [],
    embedding_model: 'bge-m3',
    chunk_size: 512,
    chunk_overlap: 64,
    document_count: 2,
    chunk_count: 8,
    size_bytes: 102400,
    created_at: now(),
    updated_at: now(),
  };

  const docs: KnowledgeDocument[] = [
    {
      id: 'doc_1', kb_id: kb.id, name: '产品手册.pdf', source: 'upload',
      mime_type: 'application/pdf', size_bytes: 51200,
      status: 'indexed', progress: 100, chunk_count: 5,
      created_at: now(), updated_at: now(), indexed_at: now(),
    },
    {
      id: 'doc_2', kb_id: kb.id, name: '发布说明.md', source: 'upload',
      mime_type: 'text/markdown', size_bytes: 51200,
      status: 'indexed', progress: 100, chunk_count: 3,
      created_at: now(), updated_at: now(), indexed_at: now(),
    },
  ];

  const chunks: DocumentChunk[] = [
    { id: 'chunk_1', document_id: 'doc_1', index: 0, content: 'v2.0 版本引入了多模态输入支持,用户可以上传图片、PDF 进行问答。', metadata: { page: 12 }, token_count: 32 },
    { id: 'chunk_2', document_id: 'doc_1', index: 1, content: '本地模型可通过 Ollama 接入,支持 Qwen、Llama、DeepSeek 等开源模型。', metadata: { page: 13 }, token_count: 30 },
    { id: 'chunk_3', document_id: 'doc_2', index: 0, content: '离线环境配置请参考部署指南附录,需提前下载模型权重和向量化模型。', metadata: {}, token_count: 28 },
  ];

  const tools: ToolDefinition[] = [
    {
      id: 'tool_web_search', name: 'web_search', description: '搜索互联网获取最新信息',
      category: 'search', is_dangerous: false,
      parameters_schema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
    },
    {
      id: 'tool_code_exec', name: 'code_execution', description: '执行 Python 代码',
      category: 'compute', is_dangerous: true,
      parameters_schema: { type: 'object', properties: { code: { type: 'string' } }, required: ['code'] },
    },
  ];

  const models: ModelEndpoint[] = [
    {
      id: 'model_qwen', name: 'Qwen 2.5 (本地)', provider: 'ollama',
      base_url: 'http://localhost:11434', model_name: 'qwen2.5:14b',
      api_key_set: false, context_window: 32768,
      capabilities: ['chat', 'tool_use'], enabled: true, created_at: now(),
    },
  ];

  const agent: Agent = {
    id: 'agent_demo', name: '产品手册助手',
    description: '基于产品手册回答问题',
    visibility: 'workspace',
    owner_id: owner.id, collaborators: [],
    system_prompt: '你是一名产品助手,根据知识库回答用户问题,引用来源。',
    opening_message: '你好!我是产品助手,有任何问题都可以问我。',
    model: { model_id: 'model_qwen', temperature: 0.3, max_tokens: 2048, top_p: 0.9 },
    retrieval: { kb_ids: [kb.id], top_k: 4, score_threshold: 0.5, hybrid: true, rerank: false },
    tools: [{ tool_id: 'tool_web_search', enabled: false, config: {} }],
    advanced: { context_window: 8192, timeout_seconds: 60, max_retries: 1, enable_streaming: true },
    created_at: now(), updated_at: now(),
  };

  return {
    users: [owner, member],
    passwords: { 'owner@example.com': 'admin123', 'member@example.com': 'member123' },
    knowledgeBases: [kb],
    documents: docs,
    chunks,
    agents: [agent],
    sessions: [],
    messages: [],
    models,
    tools,
    auditLogs: [],
  };
}

// ---- 加载 / 保存 ----
function load(): MockDB {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as MockDB;
  } catch {
    /* ignore */
  }
  const fresh = seed();
  save(fresh);
  return fresh;
}

function save(db: MockDB) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(db));
}

export const db = {
  get: load,
  save,
  reset: () => {
    const fresh = seed();
    save(fresh);
    return fresh;
  },
  uid,
  now,
};

/** 包装一次写操作:读取 → 修改 → 保存 */
export function transact<T>(fn: (db: MockDB) => T): T {
  const data = load();
  const result = fn(data);
  save(data);
  return result;
}
