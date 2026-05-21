import { http, HttpResponse } from 'msw';
import { User } from '@/types';
import { db, transact } from '../db';
import { fail, getPageParams, ok, paginate, requireAuth } from '../utils';

const BASE = '/api/v1';

function ensureAdmin(user: User): boolean {
  return user.role === 'owner' || user.role === 'admin';
}

export const userHandlers = [
  http.get(`${BASE}/users`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);
    const { page, page_size, q } = getPageParams(new URL(request.url));
    let list = db.get().users;
    if (q) list = list.filter((u) => u.name.includes(q) || u.email.includes(q));
    return ok(paginate(list, page, page_size));
  }),

  http.post(`${BASE}/users`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);

    const body = (await request.json()) as Partial<User> & { password: string };
    if (!body.email || !body.password) return fail(40000, '邮箱和密码必填', 400);

    const created = transact((d) => {
      const u: User = {
        id: db.uid('user'),
        email: body.email!,
        name: body.name || body.email!,
        avatar_url: body.avatar_url,
        role: body.role || 'member',
        status: 'active',
        created_at: db.now(),
        updated_at: db.now(),
      };
      d.users.push(u);
      d.passwords[u.email] = body.password;
      return u;
    });
    return ok(created);
  }),

  http.patch(`${BASE}/users/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);
    const patch = (await request.json()) as Partial<User>;
    const updated = transact((d) => {
      const u = d.users.find((x) => x.id === params.id);
      if (!u) return null;
      Object.assign(u, patch, { updated_at: db.now() });
      return u;
    });
    if (!updated) return fail(40404, '用户不存在', 404);
    return ok(updated);
  }),

  http.delete(`${BASE}/users/:id`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);
    transact((d) => {
      d.users = d.users.filter((x) => x.id !== params.id);
    });
    return ok(null);
  }),

  http.post(`${BASE}/users/:id/reset-password`, async ({ request, params }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);
    const tempPassword = 'temp_' + Math.random().toString(36).slice(2, 10);
    transact((d) => {
      const u = d.users.find((x) => x.id === params.id);
      if (u) d.passwords[u.email] = tempPassword;
    });
    return ok({ temp_password: tempPassword });
  }),
];

export const auditHandlers = [
  http.get(`${BASE}/audit-logs`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    if (!ensureAdmin(r.user)) return fail(40301, '权限不足', 403);
    const { page, page_size } = getPageParams(new URL(request.url));
    const list = db.get().auditLogs;
    return ok(paginate(list, page, page_size));
  }),
];
