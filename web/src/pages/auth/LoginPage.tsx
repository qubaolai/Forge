import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, Mail, Lock, Loader2 } from 'lucide-react';
import { authApi } from '@/api';
import { useAuthStore } from '@/store/auth';

export default function LoginPage() {
  const [email, setEmail] = useState('lamp.cyan@gmail.com');
  const [password, setPassword] = useState('qweasd!A');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit() {
    if (!email.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await authApi.login({ email, password });
      useAuthStore.getState().setUser(resp.user);
      useAuthStore.getState().setAccessToken(resp.access_token, resp.expires_at);
      // 管理员进管理后台；普通用户进新对话页
      const isAdmin = resp.user.role === 'admin' || resp.user.role === 'owner';
      navigate(isAdmin ? '/admin/models' : '/chat/new');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex h-screen items-center justify-center overflow-hidden bg-gray-50">
      {/* 背景柔和橙色点缀 */}
      <div className="pointer-events-none absolute -top-32 -right-24 h-96 w-96 rounded-full bg-orange-200/40 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -left-24 h-96 w-96 rounded-full bg-amber-100/50 blur-3xl" />

      <div className="relative w-full max-w-sm px-6">
        {/* 品牌标识 */}
        <div className="mb-8 flex flex-col items-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-orange-400 to-orange-600 shadow-lg shadow-orange-500/25">
            <Sparkles size={26} className="text-white" />
          </div>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight text-gray-900">Forge</h1>
        </div>

        {/* 登录卡片 */}
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-xl shadow-gray-200/50">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSubmit();
            }}
            className="flex flex-col gap-4"
          >
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-gray-600">邮箱</span>
              <div className="relative">
                <Mail
                  size={16}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
                />
                <input
                  type="email"
                  autoComplete="email"
                  className="w-full rounded-lg border border-gray-200 py-2.5 pl-9 pr-3 text-sm outline-none transition-colors focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-gray-600">密码</span>
              <div className="relative">
                <Lock
                  size={16}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
                />
                <input
                  type="password"
                  autoComplete="current-password"
                  className="w-full rounded-lg border border-gray-200 py-2.5 pl-9 pr-3 text-sm outline-none transition-colors focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </label>

            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !email.trim() || !password}
              className="mt-1 flex items-center justify-center gap-2 rounded-lg bg-orange-500 py-2.5 text-sm font-medium text-white transition-colors hover:bg-orange-600 disabled:opacity-50"
            >
              {loading && <Loader2 size={15} className="animate-spin" />}
              {loading ? '登录中…' : '登录'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
