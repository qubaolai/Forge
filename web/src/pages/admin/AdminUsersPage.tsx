import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  MoreVertical, Plus, Search, UserPlus, KeyRound, Trash2, Ban, CheckCircle2,
} from 'lucide-react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { usersApi } from '@/api';
import { confirm } from '@/components/common/ConfirmDialog';
import { ApiError, User, UserRole, UserStatus } from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';

const ROLE_LABEL: Record<UserRole, string> = {
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  guest: '访客',
};
const STATUS_LABEL: Record<UserStatus, string> = {
  active: '活跃',
  disabled: '已禁用',
  pending: '待激活',
};

export default function AdminUsersPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-users', page, q],
    queryFn: () => usersApi.list({ page, page_size: 20, q }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">用户管理</h1>
            <p className="text-sm text-gray-500 mt-1">管理系统用户、角色和权限</p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm
              bg-black text-white rounded-md hover:bg-gray-800"
          >
            <Plus size={14} />
            新增用户
          </button>
        </div>

        <div className="mb-4 relative max-w-xs">
          <Search
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400"
          />
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
            placeholder="搜索姓名或邮箱"
            className="w-full pl-8 pr-3 py-1.5 text-sm border rounded-md outline-none
              focus:border-gray-400"
          />
        </div>

        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">姓名</th>
                <th className="text-left font-medium px-4 py-2.5">邮箱</th>
                <th className="text-left font-medium px-4 py-2.5">角色</th>
                <th className="text-left font-medium px-4 py-2.5">状态</th>
                <th className="text-left font-medium px-4 py-2.5">创建时间</th>
                <th className="w-12"></th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={6} className="text-center text-gray-400 py-12">
                    加载中…
                  </td>
                </tr>
              )}
              {!isLoading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center text-gray-400 py-12">
                    暂无用户
                  </td>
                </tr>
              )}
              {data?.items.map((u) => (
                <UserRow key={u.id} user={u} />
              ))}
            </tbody>
          </table>
        </div>

        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between mt-3 text-xs text-gray-500">
            <span>
              共 {data.total} 条 · 第 {data.page} / {Math.ceil(data.total / data.page_size)} 页
            </span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                上一页
              </button>
              <button
                disabled={page >= Math.ceil(data.total / data.page_size)}
                onClick={() => setPage((p) => p + 1)}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </div>

      {creating && <CreateDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function UserRow({ user }: { user: User }) {
  const qc = useQueryClient();
  const toggleStatus = useMutation({
    mutationFn: () =>
      usersApi.update(user.id, {
        status: user.status === 'active' ? 'disabled' : 'active',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      toast.success('已更新');
    },
    onError: (e) => toast.error((e as ApiError).message || '更新失败'),
  });
  const remove = useMutation({
    mutationFn: () => usersApi.remove(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      toast.success('已删除');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });
  const resetPwd = useMutation({
    mutationFn: () => usersApi.resetPassword(user.id),
    onSuccess: (r) => {
      window.prompt('已生成临时密码,请复制并妥善保管:', r.temp_password);
    },
    onError: (e) => toast.error((e as ApiError).message || '重置失败'),
  });

  const statusColor = {
    active: 'bg-green-50 text-green-700 border-green-200',
    disabled: 'bg-gray-100 text-gray-500 border-gray-200',
    pending: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  }[user.status];

  return (
    <tr className="border-t hover:bg-gray-50">
      <td className="px-4 py-2.5">{user.name}</td>
      <td className="px-4 py-2.5 text-gray-600">{user.email}</td>
      <td className="px-4 py-2.5">
        <span className="inline-flex px-1.5 py-0.5 rounded text-[11px] bg-gray-100 text-gray-700">
          {ROLE_LABEL[user.role] || user.role}
        </span>
      </td>
      <td className="px-4 py-2.5">
        <span
          className={cn(
            'inline-flex px-1.5 py-0.5 rounded border text-[11px]',
            statusColor,
          )}
        >
          {STATUS_LABEL[user.status] || user.status}
        </span>
      </td>
      <td className="px-4 py-2.5 text-gray-500 text-xs">
        {new Date(user.created_at).toLocaleString()}
      </td>
      <td className="px-2">
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="p-1 rounded hover:bg-gray-200">
              <MoreVertical size={14} />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              align="end"
              className="bg-white border rounded-md shadow-md py-1 text-sm min-w-[140px]"
            >
              <DropdownMenu.Item
                onSelect={() => resetPwd.mutate()}
                className="px-3 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-gray-50 outline-none"
              >
                <KeyRound size={13} /> 重置密码
              </DropdownMenu.Item>
              <DropdownMenu.Item
                onSelect={() => toggleStatus.mutate()}
                className="px-3 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-gray-50 outline-none"
              >
                {user.status === 'active' ? (
                  <>
                    <Ban size={13} /> 禁用
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={13} /> 启用
                  </>
                )}
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="h-px bg-gray-100 my-1" />
              <DropdownMenu.Item
                onSelect={async () => {
                  if (await confirm({ message: `确定删除用户 ${user.email}?`, confirmLabel: '删除', danger: true })) remove.mutate();
                }}
                className="px-3 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-red-50 text-red-600 outline-none"
              >
                <Trash2 size={13} /> 删除
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </td>
    </tr>
  );
}

function CreateDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('member');

  const create = useMutation({
    mutationFn: () =>
      usersApi.create({
        email: email.trim(),
        name: name.trim(),
        password,
        role,
        status: 'active',
        avatar_url: undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      toast.success('用户创建成功');
      onClose();
    },
    onError: (e) => toast.error((e as ApiError).message || '创建失败'),
  });

  const canSubmit =
    email.trim().length > 0 && name.trim().length > 0 && password.length >= 6;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-lg shadow-xl w-full max-w-md p-5"
      >
        <h2 className="text-base font-semibold mb-4 flex items-center gap-2">
          <UserPlus size={16} /> 新增用户
        </h2>
        <div className="space-y-3">
          <Field label="邮箱 *">
            <input
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <Field label="姓名 *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <Field label="初始密码 * (至少 6 位)">
            <input
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 font-mono"
            />
          </Field>
          <Field label="角色">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 bg-white"
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
              <option value="guest">访客</option>
            </select>
          </Field>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50"
          >
            取消
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={!canSubmit || create.isPending}
            className="px-3 py-1.5 text-sm bg-black text-white rounded-md hover:bg-gray-800 disabled:opacity-50"
          >
            {create.isPending ? '创建中…' : '创建'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{label}</label>
      {children}
    </div>
  );
}
