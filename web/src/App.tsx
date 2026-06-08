import { useEffect, useRef, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { RequireAuth, RequireRole } from '@/hooks/usePermission';
import { useAuthStore } from '@/store/auth';
import { authApi } from '@/api';

// Pages(实际开发时拆到独立文件,这里只示意路由结构)
import LoginPage from '@/pages/auth/LoginPage';
import ChatPage from '@/pages/chat/ChatPage';
import KnowledgeListPage from '@/pages/knowledge/KnowledgeListPage';
import KnowledgeDetailPage from '@/pages/knowledge/KnowledgeDetailPage';
import AgentListPage from '@/pages/agents/AgentListPage';
import AgentEditorPage from '@/pages/agents/AgentEditorPage';
import SettingsPage from '@/pages/settings/SettingsPage';
import AdminUsersPage from '@/pages/admin/AdminUsersPage';
import AdminModelsPage from '@/pages/admin/AdminModelsPage';
import AdminProvidersPage from '@/pages/admin/AdminProvidersPage';
import AdminAuditPage from '@/pages/admin/AdminAuditPage';
import AppLayout from '@/components/common/AppLayout';
import { ToastContainer } from '@/components/common/Toast';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

/**
 * 启动时尝试用 refresh cookie 拿一次 access token, 然后把 hydrated 置 true。
 * 不论成功失败, 都让路由保护层走到"最终判断"分支, 避免一直 loading。
 */
function AuthHydrator({ children }: { children: React.ReactNode }) {
  const hydrated = useAuthStore((s) => s.hydrated);
  const setHydrated = useAuthStore((s) => s.setHydrated);
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setUser = useAuthStore((s) => s.setUser);
  const persistedUser = useAuthStore((s) => s.user);

  useEffect(() => {
    if (hydrated) return;
    let cancelled = false;
    (async () => {
      // 没有持久化的 user, 没必要尝试 refresh, 直接判定为未登录
      if (!persistedUser) {
        if (!cancelled) setHydrated(true);
        return;
      }
      try {
        const tokens = await authApi.refresh();
        if (cancelled) return;
        setAccessToken(tokens.access_token, tokens.expires_at);
        // 顺便刷新当前用户信息 (角色可能在服务端被改过)
        try {
          const me = await authApi.me();
          if (!cancelled) setUser(me);
        } catch {
          /* 拿不到 me 不阻塞, 已有 persisted user 兜底 */
        }
      } catch {
        // refresh 失败 → 走未登录
        if (!cancelled) {
          useAuthStore.getState().clear();
        }
      } finally {
        if (!cancelled) setHydrated(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hydrated, persistedUser, setAccessToken, setUser, setHydrated]);

  return <>{children}</>;
}

/**
 * access token 主动刷新: 到期前 60 秒用 refresh cookie 换新 token.
 * 提前刷新避免 SSE 长连接中途 token 过期.
 */
function TokenRefresher() {
  const hydrated = useAuthStore((s) => s.hydrated);
  const accessToken = useAuthStore((s) => s.accessToken);
  const expiresAt = useAuthStore((s) => s.expiresAt);
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!hydrated || !accessToken || !expiresAt) return;

    const expiresMs = new Date(expiresAt).getTime();
    const delay = expiresMs - Date.now() - 60_000;  // 到期前 1 分钟刷新

    if (delay <= 0) return;  // 已过期/即将过期, 交给 401 拦截器处理

    timerRef.current = setTimeout(async () => {
      try {
        const tokens = await authApi.refresh();
        setAccessToken(tokens.access_token, tokens.expires_at);
      } catch {
        /* 静默失败, 后续请求由 401 拦截器兜底 */
      }
    }, delay);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [hydrated, accessToken, expiresAt, setAccessToken]);

  return null;
}

/** 角色感知的首页落地：管理员进管理后台，普通用户进对话。 */
function HomeRedirect() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin' || user?.role === 'owner';
  return <Navigate to={isAdmin ? '/admin/models' : '/chat'} replace />;
}

/** 普通用户专属路由：管理员一律重定向到管理后台（管理员不展示对话等页面）。 */
function UserOnly({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin' || user?.role === 'owner';
  if (isAdmin) return <Navigate to="/admin/models" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastContainer />
      <ConfirmDialog />
      <BrowserRouter>
        <AuthHydrator>
          <TokenRefresher />
          <Routes>
            <Route path="/login" element={<LoginPage />} />

            <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
              <Route index element={<HomeRedirect />} />

              {/* 普通用户路由：管理员一律重定向到管理后台 */}
              <Route element={<UserOnly><Outlet /></UserOnly>}>
                <Route path="chat">
                  <Route index element={<ChatPage />} />
                  <Route path=":sessionId" element={<ChatPage />} />
                </Route>

                <Route path="knowledge">
                  <Route index element={<KnowledgeListPage />} />
                  <Route path=":kbId" element={<KnowledgeDetailPage />} />
                </Route>

                <Route path="agents">
                  <Route index element={<AgentListPage />} />
                  <Route path="new" element={<AgentEditorPage />} />
                  <Route path=":agentId" element={<AgentEditorPage />} />
                </Route>

                <Route path="settings/*" element={<SettingsPage />} />
              </Route>

              <Route path="admin" element={<RequireRole roles={['owner', 'admin']}><Outlet /></RequireRole>}>
                <Route path="users" element={<AdminUsersPage />} />
                <Route path="models" element={<AdminModelsPage />} />
                <Route path="providers" element={<AdminProvidersPage />} />
                <Route path="audit" element={<AdminAuditPage />} />
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthHydrator>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
