import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { User, UserRole } from '@/types';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  expiresAt: string | null;

  /** 启动时是否完成过 access token 刷新尝试。
   * - false: 仍在尝试用 refresh cookie 拿新 access token, 路由保护层应该等待
   * - true:  尝试已结束 (成功 / 失败都算), 路由保护层可以做最终判断
   */
  hydrated: boolean;

  setUser: (user: User | null) => void;
  setAccessToken: (token: string | null, expiresAt?: string) => void;
  setHydrated: (v: boolean) => void;
  clear: () => void;

  // 派生方法
  isAuthenticated: () => boolean;
  hasRole: (...roles: UserRole[]) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      expiresAt: null,
      hydrated: false,

      setUser: (user) => set({ user }),
      setAccessToken: (token, expiresAt) =>
        set({ accessToken: token, expiresAt: expiresAt ?? null }),
      setHydrated: (v) => set({ hydrated: v }),
      clear: () =>
        set({
          user: null,
          accessToken: null,
          expiresAt: null,
          // clear 不动 hydrated, 否则会让路由保护回到 loading
        }),

      isAuthenticated: () => !!get().accessToken && !!get().user,
      hasRole: (...roles) => {
        const r = get().user?.role;
        return !!r && roles.includes(r);
      },
    }),
    {
      name: 'rag-auth',
      // access token 不持久化 (放内存); 仅持久化用户信息.
      // 刷新页面后通过 refresh cookie 重新拿 access token (App 启动时尝试).
      partialize: (s) => ({ user: s.user }),
    },
  ),
);
