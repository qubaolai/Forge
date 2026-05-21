import { HttpResponse, delay } from 'msw';
import { User } from '@/types';
import { db } from './db';

/** 模拟网络延时 */
export const FAKE_LATENCY = 200;

/** 包装成功响应:返回类型用 HttpResponse(去掉泛型),避免 MSW 把 handler 类型推断到具体子类型 */
export function ok<T>(data: T, latency = FAKE_LATENCY): Promise<HttpResponse<any>> {
  return delay(latency).then(() =>
    HttpResponse.json({ code: 0, data, message: 'ok' }),
  );
}

export function fail(code: number, message: string, status = 400, latency = FAKE_LATENCY): Promise<HttpResponse<any>> {
  return delay(latency).then(() =>
    HttpResponse.json({ code, data: null, message }, { status }),
  );
}

/** 简单 token 校验:从 Authorization header 取出 user_id */
export function authUser(req: Request): User | null {
  const auth = req.headers.get('Authorization');
  if (!auth?.startsWith('Bearer ')) return null;
  const token = auth.slice(7);
  if (!token.startsWith('mock_')) return null;
  const userId = token.slice(5);
  return db.get().users.find((u) => u.id === userId) || null;
}

/**
 * 校验权限,失败返回 401/403 响应,成功返回 user
 * 注意:返回类型联合 HttpResponse 而非 Response
 */
export async function requireAuth(req: Request): Promise<{ user: User } | HttpResponse<any>> {
  const user = authUser(req);
  if (!user) return await fail(401, '未登录或登录已过期', 401);
  return { user };
}

export function paginate<T>(arr: T[], page = 1, pageSize = 20) {
  const start = (page - 1) * pageSize;
  return {
    items: arr.slice(start, start + pageSize),
    total: arr.length,
    page,
    page_size: pageSize,
  };
}

export function getPageParams(url: URL) {
  return {
    page: Number(url.searchParams.get('page') || 1),
    page_size: Number(url.searchParams.get('page_size') || 20),
    q: url.searchParams.get('q') || '',
  };
}
