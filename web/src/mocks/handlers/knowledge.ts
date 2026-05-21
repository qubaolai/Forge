import { http, HttpResponse } from 'msw';
import { KnowledgeBase, RetrieveRequest } from '@/types';
import { db, transact } from '../db';
import { fail, getPageParams, ok, paginate, requireAuth } from '../utils';

const BASE = '/api/v1';

export const knowledgeHandlers = [
  // 列表
  http.get(`${BASE}/knowledge-bases`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const { page, page_size, q } = getPageParams(new URL(request.url));
    let list = db.get().knowledgeBases;
    if (q) list = list.filter((x) => x.name.includes(q));
    return ok(paginate(list, page, page_size));
  }),

  // 创建
  http.post(`${BASE}/knowledge-bases`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const body = (await request.json()) as Partial<KnowledgeBase>;
    const created = transact((d) => {
      const kb: KnowledgeBase = {
        id: db.uid('kb'),
        name: body.name || '未命名知识库',
        description: body.description,
        visibility: body.visibility || 'private',
        owner_id: r.user.id,
        collaborators: [],
        embedding_model: body.embedding_model || 'bge-m3',
        chunk_size: body.chunk_size || 512,
        chunk_overlap: body.chunk_overlap || 64,
        document_count: 0,
        chunk_count: 0,
        size_bytes: 0,
        created_at: db.now(),
        updated_at: db.now(),
      };
      d.knowledgeBases.push(kb);
      return kb;
    });
    return ok(created);
  }),

  // 详情
  http.get(`${BASE}/knowledge-bases/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const kb = db.get().knowledgeBases.find((x) => x.id === params.id);
    if (!kb) return fail(40404, '知识库不存在', 404);
    return ok(kb);
  }),

  // 更新
  http.patch(`${BASE}/knowledge-bases/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const patch = (await request.json()) as Partial<KnowledgeBase>;
    const updated = transact((d) => {
      const kb = d.knowledgeBases.find((x) => x.id === params.id);
      if (!kb) return null;
      Object.assign(kb, patch, { updated_at: db.now() });
      return kb;
    });
    if (!updated) return fail(40404, '知识库不存在', 404);
    return ok(updated);
  }),

  // 删除
  http.delete(`${BASE}/knowledge-bases/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    transact((d) => {
      d.knowledgeBases = d.knowledgeBases.filter((x) => x.id !== params.id);
      d.documents = d.documents.filter((x) => x.kb_id !== params.id);
    });
    return ok(null);
  }),

  // 检索测试(返回 top-k chunks + 文档信息)
  http.post(`${BASE}/knowledge-bases/:id/retrieve`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const body = (await request.json()) as RetrieveRequest;
    const data = db.get();
    const kbDocs = data.documents.filter((d) => d.kb_id === params.id);
    const docIds = new Set(kbDocs.map((d) => d.id));
    const candidates = data.chunks.filter((c) => docIds.has(c.document_id));

    // 简单地以"是否包含 query 关键字"作为相似度模拟
    const query = body.query.toLowerCase();
    const scored = candidates
      .map((c) => ({
        chunk: c,
        score: c.content.toLowerCase().includes(query) ? 0.9 : Math.random() * 0.5,
      }))
      .sort((a, b) => b.score - a.score)
      .slice(0, body.top_k || 4);

    const results = scored.map(({ chunk, score }) => {
      const doc = kbDocs.find((d) => d.id === chunk.document_id)!;
      return {
        chunk,
        document: { id: doc.id, name: doc.name, source_url: doc.source_url },
        score,
      };
    });
    return ok(results);
  }),

  // 文档列表
  http.get(`${BASE}/knowledge-bases/:kbId/documents`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const { page, page_size } = getPageParams(new URL(request.url));
    const list = db.get().documents.filter((d) => d.kb_id === params.kbId);
    return ok(paginate(list, page, page_size));
  }),

  // 文档详情
  http.get(`${BASE}/knowledge-bases/:kbId/documents/:docId`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const doc = db.get().documents.find((d) => d.id === params.docId);
    if (!doc) return fail(40404, '文档不存在', 404);
    return ok(doc);
  }),

  // 文档分块
  http.get(`${BASE}/knowledge-bases/:kbId/documents/:docId/chunks`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const { page, page_size } = getPageParams(new URL(request.url));
    const list = db.get().chunks.filter((c) => c.document_id === params.docId);
    return ok(paginate(list, page, page_size));
  }),

  // 上传文档(模拟解析进度)
  http.post(`${BASE}/knowledge-bases/:kbId/documents/upload`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const form = await request.formData();
    const file = form.get('file') as File | null;
    if (!file) return fail(40000, '缺少文件', 400);

    const created = transact((d) => {
      const doc = {
        id: db.uid('doc'),
        kb_id: params.kbId as string,
        name: file.name,
        source: 'upload' as const,
        mime_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
        status: 'pending' as const,
        progress: 0,
        chunk_count: 0,
        created_at: db.now(),
        updated_at: db.now(),
      };
      d.documents.push(doc);
      return doc;
    });

    // 异步模拟解析过程
    simulateParsing(created.id);
    return ok(created);
  }),

  // 删除文档
  http.delete(`${BASE}/knowledge-bases/:kbId/documents/:docId`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    transact((d) => {
      d.documents = d.documents.filter((x) => x.id !== params.docId);
      d.chunks = d.chunks.filter((c) => c.document_id !== params.docId);
    });
    return ok(null);
  }),
];

// 模拟文档解析:依次更新 status,5 秒走完整个流程
function simulateParsing(docId: string) {
  const stages: Array<{ status: 'parsing' | 'chunking' | 'embedding' | 'indexed'; progress: number }> = [
    { status: 'parsing', progress: 30 },
    { status: 'chunking', progress: 60 },
    { status: 'embedding', progress: 85 },
    { status: 'indexed', progress: 100 },
  ];
  let i = 0;
  const tick = () => {
    if (i >= stages.length) return;
    const stage = stages[i++];
    transact((d) => {
      const doc = d.documents.find((x) => x.id === docId);
      if (!doc) return;
      doc.status = stage.status;
      doc.progress = stage.progress;
      doc.updated_at = db.now();
      if (stage.status === 'indexed') {
        doc.indexed_at = db.now();
        doc.chunk_count = 3;
        // 给文档造几个 chunk
        d.chunks.push(
          { id: db.uid('chunk'), document_id: docId, index: 0, content: `${doc.name} 的内容片段 1`, metadata: {}, token_count: 32 },
          { id: db.uid('chunk'), document_id: docId, index: 1, content: `${doc.name} 的内容片段 2`, metadata: {}, token_count: 28 },
          { id: db.uid('chunk'), document_id: docId, index: 2, content: `${doc.name} 的内容片段 3`, metadata: {}, token_count: 30 },
        );
      }
    });
    setTimeout(tick, 1200);
  };
  setTimeout(tick, 800);
}
