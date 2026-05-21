import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import {
  Plus, BookOpen, Bot, Settings, LogOut,
  Users, Cpu, FileText, ChevronDown,
} from 'lucide-react';
import { useState } from 'react';
import { useAuthStore } from '@/store/auth';
import { sessionsApi, authApi } from '@/api';
import { confirm } from './ConfirmDialog';
import { cn } from '@/lib/utils';
import { FEATURES } from '@/lib/features';
import { SessionItem } from './SessionItem';

/** 会话列表查询使用的固定 queryKey, 让所有调用方都用同一个引用。 */
export const SESSIONS_QUERY_KEY = ['sessions'] as const;

export default function AppLayout() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin' || user?.role === 'owner';
  const navigate = useNavigate();
  const location = useLocation();
  const [adminOpen, setAdminOpen] = useState(false);

  // 会话列表
  const { data: sessions } = useQuery({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: () => sessionsApi.list({ page: 1, page_size: 50 }),
  });

  function handleNewChat() {
    navigate('/chat/new');
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white">
      {/* 左侧栏 */}
      <aside className="w-64 border-r flex flex-col bg-gray-50/40 shrink-0">
        {/* 顶部:Logo + 新对话 */}
        <div className="px-3 pt-3 pb-2">
          <div className="flex items-center gap-2 px-2 py-1.5 mb-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center text-white text-xs font-bold">
              R
            </div>
            <span className="text-sm font-medium">RAG Agent</span>
          </div>
          <button
            onClick={handleNewChat}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm
              bg-white border border-gray-200 hover:bg-gray-50 transition-colors"
          >
            <Plus size={14} />
            <span>新对话</span>
          </button>
        </div>

        {/* 中部:会话列表 */}
        <div className="flex-1 overflow-y-auto px-2 py-1 min-h-0">
          {sessions && sessions.items.length > 0 && (
            <div className="px-2 py-1.5 text-[11px] text-gray-400 font-medium">
              最近对话
            </div>
          )}
          {sessions?.items.map((s) => (
            <SessionItem
              key={s.id}
              session={s}
              active={location.pathname === `/chat/${s.id}`}
            />
          ))}
          {(!sessions || sessions.items.length === 0) && (
            <div className="px-3 py-6 text-xs text-gray-400 text-center">
              还没有对话<br />点击"新对话"开始
            </div>
          )}
        </div>

        {/* 底部:模块入口 + 用户信息 */}
        <div className="border-t bg-gray-50/40 px-2 py-2 shrink-0">
          <ModuleNavItem to="/knowledge" icon={<BookOpen size={14} />} label="知识库" />
          {FEATURES.AGENTS && (
            <ModuleNavItem to="/agents" icon={<Bot size={14} />} label="Agent" />
          )}
          <ModuleNavItem to="/settings" icon={<Settings size={14} />} label="设置" />

          {FEATURES.ADMIN && isAdmin && (
            <>
              <button
                onClick={() => setAdminOpen((v) => !v)}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md
                  text-sm text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <ChevronDown size={14} className={cn('transition-transform', !adminOpen && '-rotate-90')} />
                <span>管理</span>
              </button>
              {adminOpen && (
                <div className="ml-2 border-l border-gray-200 pl-1">
                  <ModuleNavItem to="/admin/users" icon={<Users size={14} />} label="用户" />
                  <ModuleNavItem to="/admin/models" icon={<Cpu size={14} />} label="模型" />
                  <ModuleNavItem to="/admin/audit" icon={<FileText size={14} />} label="审计日志" />
                </div>
              )}
            </>
          )}

          {/* 用户信息 + 登出 */}
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="w-full mt-2 pt-2 border-t border-gray-200/60 flex items-center gap-2 px-2.5 py-1.5 hover:bg-gray-100 rounded-md transition-colors">
                <div className="w-6 h-6 rounded-full bg-blue-100 text-blue-700
                  flex items-center justify-center text-xs font-medium shrink-0">
                  {user?.name?.[0]?.toUpperCase() || '?'}
                </div>
                <div className="flex-1 min-w-0 text-left">
                  <div className="text-xs font-medium truncate">{user?.name}</div>
                  <div className="text-[10px] text-gray-400 truncate">{user?.role}</div>
                </div>
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                side="top"
                align="start"
                sideOffset={4}
                className="z-50 min-w-[180px] bg-white border rounded-md shadow-md py-1 text-sm"
              >
                <DropdownMenu.Item
                  onSelect={() => navigate('/settings')}
                  className="flex items-center gap-2 px-3 py-1.5 cursor-pointer outline-none
                    hover:bg-gray-100 data-[highlighted]:bg-gray-100"
                >
                  <Settings size={13} />
                  设置
                </DropdownMenu.Item>
                <DropdownMenu.Separator className="h-px bg-gray-200 my-1" />
                <DropdownMenu.Item
                  onSelect={async () => {
                    if (!await confirm({ message: '确认要登出吗?', confirmLabel: '登出', danger: true })) return;
                    try {
                      await authApi.logout();
                    } catch { /* 忽略登出失败 */ }
                    useAuthStore.getState().clear();
                    navigate('/login');
                  }}
                  className="flex items-center gap-2 px-3 py-1.5 cursor-pointer outline-none
                    text-red-600 hover:bg-red-50 data-[highlighted]:bg-red-50"
                >
                  <LogOut size={13} />
                  登出
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-hidden min-w-0">
        <Outlet />
      </main>
    </div>
  );
}

function ModuleNavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors',
          isActive
            ? 'bg-gray-200/70 text-gray-900'
            : 'text-gray-600 hover:bg-gray-100',
        )
      }
    >
      {icon}
      <span>{label}</span>
    </NavLink>
  );
}
