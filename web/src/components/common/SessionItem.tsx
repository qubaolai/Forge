import { useState } from 'react';
import { useNavigate, NavLink } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { MessageSquare, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import { ChatSession, PaginatedData } from '@/types';
import { sessionsApi } from '@/api';
import { cn } from '@/lib/utils';
import { SESSIONS_QUERY_KEY } from './AppLayout';
import { confirm } from './ConfirmDialog';
import { toast } from './Toast';

interface Props {
  session: ChatSession;
  active: boolean;
}

export function SessionItem({ session, active }: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);

  const renameMutation = useMutation({
    mutationFn: (newTitle: string) => sessionsApi.update(session.id, { title: newTitle }),
    onSuccess: (updated) => {
      // 乐观更新当前列表中的标题
      qc.setQueryData<PaginatedData<ChatSession>>(SESSIONS_QUERY_KEY, (prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((s) => (s.id === session.id ? { ...s, ...updated } : s)),
        };
      });
      qc.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
      qc.invalidateQueries({ queryKey: ['session', session.id] });
      setRenaming(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => sessionsApi.remove(session.id),
    // onMutate：API 还没回来就先做乐观更新，用户瞬间看到条目消失
    onMutate: async () => {
      // 取消正在进行的 refetch，避免覆盖乐观更新
      await qc.cancelQueries({ queryKey: SESSIONS_QUERY_KEY });
      const snapshot = qc.getQueryData<PaginatedData<ChatSession>>(SESSIONS_QUERY_KEY);
      qc.setQueryData<PaginatedData<ChatSession>>(SESSIONS_QUERY_KEY, (prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.filter((s) => s.id !== session.id),
          total: Math.max(0, prev.total - 1),
        };
      });
      if (active) navigate('/chat');
      return { snapshot };
    },
    onError: (err, _vars, ctx) => {
      // 失败回滚
      if (ctx?.snapshot) {
        qc.setQueryData(SESSIONS_QUERY_KEY, ctx.snapshot);
      }
      toast.error('删除会话失败: ' + (err instanceof Error ? err.message : String(err)));
    },
    onSettled: () => {
      // 不管成功/失败都拉一次确保最终一致
      qc.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
      qc.removeQueries({ queryKey: ['session', session.id] });
      qc.removeQueries({ queryKey: ['session-messages', session.id] });
    },
  });

  function commitRename() {
    const trimmed = title.trim();
    if (!trimmed || trimmed === session.title) {
      setTitle(session.title);
      setRenaming(false);
      return;
    }
    renameMutation.mutate(trimmed);
  }

  // 重命名模式:渲染输入框
  if (renaming) {
    return (
      <div
        className={cn(
          'flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm',
          active ? 'bg-gray-200/70' : 'bg-gray-100',
        )}
      >
        <MessageSquare size={13} className="shrink-0 opacity-60" />
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename();
            if (e.key === 'Escape') {
              setTitle(session.title);
              setRenaming(false);
            }
          }}
          className="flex-1 bg-white border border-gray-300 rounded px-1.5 py-0.5 text-sm outline-none focus:border-blue-400"
        />
      </div>
    );
  }

  return (
    // 行容器不是 NavLink: 只有标题区可跳转, ⋯ 菜单作为兄弟节点, 点它/删除不会跳进会话
    <div
      className={cn(
        'group relative flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors',
        active
          ? 'bg-gray-200/70 text-gray-900'
          : 'text-gray-600 hover:bg-gray-100',
        // 菜单打开时保持 hover 视觉
        menuOpen && !active && 'bg-gray-100',
      )}
    >
      <NavLink
        to={`/chat/${session.id}`}
        className="flex min-w-0 flex-1 items-center gap-2 truncate"
      >
        <MessageSquare size={13} className="shrink-0 opacity-60" />
        <span className="truncate flex-1">{session.title}</span>
      </NavLink>

      <DropdownMenu.Root open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenu.Trigger asChild>
          <button
            onClick={(e) => e.stopPropagation()}
            className={cn(
              'shrink-0 p-0.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-200/70',
              'opacity-0 group-hover:opacity-100',
              menuOpen && 'opacity-100',
            )}
          >
            <MoreHorizontal size={14} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="start"
            sideOffset={4}
            className="z-50 min-w-[120px] bg-white border rounded-md shadow-md py-1 text-sm"
          >
            <DropdownMenu.Item
              onSelect={() => {
                setTitle(session.title);
                setRenaming(true);
              }}
              className="flex items-center gap-2 px-3 py-1.5 cursor-pointer outline-none
                hover:bg-gray-100 data-[highlighted]:bg-gray-100"
            >
              <Pencil size={13} />
              重命名
            </DropdownMenu.Item>
            <DropdownMenu.Item
              onSelect={async () => {
                if (await confirm({ message: `删除会话「${session.title}」?`, confirmLabel: '删除', danger: true })) {
                  deleteMutation.mutate();
                }
              }}
              className="flex items-center gap-2 px-3 py-1.5 cursor-pointer outline-none
                text-red-600 hover:bg-red-50 data-[highlighted]:bg-red-50"
            >
              <Trash2 size={13} />
              删除
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}
