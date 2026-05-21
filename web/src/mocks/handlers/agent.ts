import { http, HttpResponse } from 'msw';
import { Agent } from '@/types';
import { db, transact } from '../db';
import { fail, getPageParams, ok, paginate, requireAuth } from '../utils';

const BASE = '/api/v1';

export const agentHandlers = [
  http.get(`${BASE}/agents`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const { page, page_size, q } = getPageParams(new URL(request.url));
    let list = db.get().agents;
    if (q) list = list.filter((x) => x.name.includes(q));
    return ok(paginate(list, page, page_size));
  }),

  http.post(`${BASE}/agents`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const body = (await request.json()) as Partial<Agent>;
    const created = transact((d) => {
      const agent: Agent = {
        id: db.uid('agent'),
        name: body.name || '未命名 Agent',
        description: body.description,
        visibility: body.visibility || 'private',
        owner_id: r.user.id,
        collaborators: [],
        system_prompt: body.system_prompt || '你是一个智能助手。',
        opening_message: body.opening_message,
        model: body.model || { model_id: 'model_qwen', temperature: 0.3, max_tokens: 2048, top_p: 0.9 },
        retrieval: body.retrieval || { kb_ids: [], top_k: 4, score_threshold: 0.5, hybrid: true, rerank: false },
        tools: body.tools || [],
        advanced: body.advanced || { context_window: 8192, timeout_seconds: 60, max_retries: 1, enable_streaming: true },
        created_at: db.now(),
        updated_at: db.now(),
      };
      d.agents.push(agent);
      return agent;
    });
    return ok(created);
  }),

  http.get(`${BASE}/agents/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const agent = db.get().agents.find((x) => x.id === params.id);
    if (!agent) return fail(40404, 'Agent 不存在', 404);
    return ok(agent);
  }),

  http.patch(`${BASE}/agents/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const patch = (await request.json()) as Partial<Agent>;
    const updated = transact((d) => {
      const a = d.agents.find((x) => x.id === params.id);
      if (!a) return null;
      Object.assign(a, patch, { updated_at: db.now() });
      return a;
    });
    if (!updated) return fail(40404, 'Agent 不存在', 404);
    return ok(updated);
  }),

  http.delete(`${BASE}/agents/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    transact((d) => {
      d.agents = d.agents.filter((x) => x.id !== params.id);
    });
    return ok(null);
  }),

  http.post(`${BASE}/agents/:id/duplicate`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const created = transact((d) => {
      const src = d.agents.find((x) => x.id === params.id);
      if (!src) return null;
      const copy: Agent = {
        ...src,
        id: db.uid('agent'),
        name: src.name + ' 副本',
        created_at: db.now(),
        updated_at: db.now(),
      };
      d.agents.push(copy);
      return copy;
    });
    if (!created) return fail(40404, 'Agent 不存在', 404);
    return ok(created);
  }),
];

export const toolHandlers = [
  http.get(`${BASE}/tools`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    return ok(db.get().tools);
  }),
];

export const modelHandlers = [
  http.get(`${BASE}/models`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    return ok(db.get().models);
  }),
];
