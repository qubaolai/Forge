import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '@/api';
import { useAuthStore } from '@/store/auth';

export default function LoginPage() {
  const [email, setEmail] = useState('lamp.cyan@gmail.com');
  const [password, setPassword] = useState('qweasd!A');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit() {
    setLoading(true);
    setError(null);
    try {
      const resp = await authApi.login({ email, password });
      useAuthStore.getState().setUser(resp.user);
      useAuthStore.getState().setAccessToken(resp.access_token, resp.expires_at);
      navigate('/');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="w-80 flex flex-col gap-3">
        <h1 className="text-xl font-medium mb-2">登录</h1>
        <input
          className="border rounded px-3 py-2"
          placeholder="邮箱"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="border rounded px-3 py-2"
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="text-sm text-red-500">{error}</div>}
        <button
          className="bg-black text-white py-2 rounded disabled:opacity-50"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading ? '登录中…' : '登录'}
        </button>
      </div>
    </div>
  );
}
