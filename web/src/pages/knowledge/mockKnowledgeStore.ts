import type { KnowledgeBase, KnowledgeDocument, RetrievalResult, Visibility } from '@/types';

const STORE_KEY = 'forge.staticKnowledge.v1';

type KnowledgeStore = {
  kbs: KnowledgeBase[];
  docs: KnowledgeDocument[];
};

const now = new Date().toISOString();

const initialStore: KnowledgeStore = {
  kbs: [
    {
      id: 'kb_product',
      name: '产品知识库',
      description: '产品规格、定价、常见问题和发布说明。',
      visibility: 'workspace',
      owner_id: 'mock_user',
      collaborators: [],
      chunk_size: 500,
      chunk_overlap: 50,
      document_count: 3,
      chunk_count: 128,
      size_bytes: 843_200,
      created_at: now,
      updated_at: now,
    },
    {
      id: 'kb_support',
      name: '客服话术库',
      description: '售前咨询、故障排查和升级处理流程。',
      visibility: 'private',
      owner_id: 'mock_user',
      collaborators: [],
      chunk_size: 600,
      chunk_overlap: 80,
      document_count: 2,
      chunk_count: 76,
      size_bytes: 421_400,
      created_at: now,
      updated_at: now,
    },
  ],
  docs: [
    makeDoc('doc_guide', 'kb_product', '产品手册.pdf', 328_000, 54, 'ready'),
    makeDoc('doc_release', 'kb_product', 'Release Notes.md', 42_200, 19, 'ready'),
    makeDoc('doc_pricing', 'kb_product', '价格策略.xlsx', 473_000, 55, 'stale'),
    makeDoc('doc_support', 'kb_support', '客服标准流程.docx', 260_100, 42, 'ready'),
    makeDoc('doc_faq', 'kb_support', '常见问题.txt', 161_300, 34, 'ready'),
  ],
};

function makeDoc(
  id: string,
  kbId: string,
  name: string,
  sizeBytes: number,
  chunkCount: number,
  vectorStatus: KnowledgeDocument['vector_index_status'],
): KnowledgeDocument {
  return {
    id,
    kb_id: kbId,
    name,
    source: 'upload',
    mime_type: 'application/octet-stream',
    size_bytes: sizeBytes,
    status: 'indexed',
    progress: 100,
    chunk_count: chunkCount,
    vector_index_status: vectorStatus,
    created_at: now,
    updated_at: now,
    indexed_at: now,
  };
}

export function loadKnowledgeStore(): KnowledgeStore {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw) as KnowledgeStore;
  } catch {
    // fall through to seed data
  }
  saveKnowledgeStore(initialStore);
  return structuredClone(initialStore);
}

export function saveKnowledgeStore(store: KnowledgeStore): void {
  window.localStorage.setItem(STORE_KEY, JSON.stringify(store));
}

export function createKnowledgeBase(
  input: { name: string; description?: string; visibility: Visibility },
): KnowledgeBase {
  const createdAt = new Date().toISOString();
  return {
    id: `kb_${Date.now()}`,
    name: input.name,
    description: input.description,
    visibility: input.visibility,
    owner_id: 'mock_user',
    collaborators: [],
    chunk_size: 500,
    chunk_overlap: 50,
    document_count: 0,
    chunk_count: 0,
    size_bytes: 0,
    created_at: createdAt,
    updated_at: createdAt,
  };
}

export function recalcKnowledgeBase(kb: KnowledgeBase, docs: KnowledgeDocument[]): KnowledgeBase {
  const ownDocs = docs.filter((d) => d.kb_id === kb.id);
  return {
    ...kb,
    document_count: ownDocs.length,
    chunk_count: ownDocs.reduce((sum, d) => sum + d.chunk_count, 0),
    size_bytes: ownDocs.reduce((sum, d) => sum + d.size_bytes, 0),
    updated_at: new Date().toISOString(),
  };
}

export function makeUploadedDocument(kbId: string, file: File): KnowledgeDocument {
  const createdAt = new Date().toISOString();
  const chunks = Math.max(1, Math.ceil(file.size / 8000));
  return {
    id: `doc_${Date.now()}_${Math.round(Math.random() * 1000)}`,
    kb_id: kbId,
    name: file.name,
    source: 'upload',
    mime_type: file.type || 'application/octet-stream',
    size_bytes: file.size,
    status: 'indexed',
    progress: 100,
    chunk_count: chunks,
    vector_index_status: 'ready',
    created_at: createdAt,
    updated_at: createdAt,
    indexed_at: createdAt,
  };
}

export function mockRetrieve(query: string, docs: KnowledgeDocument[]): RetrievalResult[] {
  if (!query.trim()) return [];
  return docs.slice(0, 5).map((doc, index) => ({
    document: { id: doc.id, name: doc.name, source_url: doc.source_url },
    score: Math.max(0.42, 0.93 - index * 0.11),
    chunk: {
      id: `chunk_${doc.id}_${index}`,
      document_id: doc.id,
      index,
      content: `这是来自《${doc.name}》的静态检索片段。当前查询是“${query}”。后续接入后端后，这里会展示真实召回内容、章节、页码和分数。`,
      metadata: { page: index + 1, section: '静态占位' },
      token_count: 96,
    },
  }));
}
