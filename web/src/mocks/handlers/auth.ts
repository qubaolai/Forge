import { http, HttpResponse } from 'msw';
// import { LoginPayload } from '@/types';
import { db } from '../db';
import { fail, ok, requireAuth } from '../utils';

const BASE = '/api/v1';

export const authHandlers = [
  // 登录
  // http.post(`${BASE}/auth/login`, async ({ request }) => {
  //   const { email, password } = (await request.json()) as LoginPayload;
  //   const data = db.get();
  //   const user = data.users.find((u) => u.email === email);
  //   if (!user) return fail(40401, '用户不存在', 401);
  //   if (data.passwords[email] !== password) return fail(40402, '密码错误', 401);
  //   if (user.status === 'disabled') return fail(40403, '账户已禁用', 403);

  //   const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  //   return ok({
  //     access_token: `mock_${user.id}`,
  //     expires_at: expiresAt,
  //     user,
  //   });
  // }),

  // // 刷新 token
  // http.post(`${BASE}/auth/refresh`, () => {
  //   // mock 始终为第一个用户刷新(实际应读 cookie)
  //   const data = db.get();
  //   const user = data.users[0];
  //   const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  //   return ok({
  //     access_token: `mock_${user.id}`,
  //     expires_at: expiresAt,
  //   });
  // }),

  // // 当前用户
  // http.get(`${BASE}/auth/me`, async ({ request }) => {
  //   const r = await requireAuth(request);
  //   if (r instanceof HttpResponse) return r;
  //   return ok(r.user);
  // }),

  // // 登出
  // http.post(`${BASE}/auth/logout`, () => ok(null)),

  // 改密码
  http.post(`${BASE}/auth/change-password`, async ({ request }) => {
    const r = await requireAuth(request);
    if (r instanceof HttpResponse) return r;
    const { old_password, new_password } = (await request.json()) as {
      old_password: string;
      new_password: string;
    };
    const data = db.get();
    if (data.passwords[r.user.email] !== old_password) return fail(40404, '原密码错误', 400);
    data.passwords[r.user.email] = new_password;
    db.save(data);
    return ok(null);
  }),

  // 占位:不让 MSW 警告未处理的 SW 请求
  http.get('/mockServiceWorker.js', () => new HttpResponse(null, { status: 404 })),
];
