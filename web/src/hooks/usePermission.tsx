import { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/store/auth';
import { Collaborator, CollaboratorPermission, UserRole } from '@/types';

export function usePermission() {
  const user = useAuthStore((s) => s.user);

  function canAccess(resource: { owner_id: string; collaborators: Collaborator[] }, required: CollaboratorPermission): boolean {
    if (!user) return false;
    if (user.role === 'owner' || user.role === 'admin') return true;
    if (resource.owner_id === user.id) return true;
    const c = resource.collaborators.find((x) => x.user_id === user.id);
    if (!c) return false;
    const order: Record<CollaboratorPermission, number> = { read: 1, write: 2, admin: 3 };
    return order[c.permission] >= order[required];
  }

  return {
    user,
    canAccess,
    hasRole: (...roles: UserRole[]) => !!user && roles.includes(user.role),
  };
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const location = useLocation();

  // 启动时还在尝试 refresh, 先展示 loading, 不要立刻跳 /login
  if (!hydrated) {
    return (
      <div className="h-screen w-screen flex items-center justify-center text-sm text-gray-400">
        加载中…
      </div>
    );
  }

  if (!accessToken || !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

export function RequireRole({ roles, children }: { roles: UserRole[]; children: ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const hasRole = !!user && roles.includes(user.role);
  if (!hasRole) return <Navigate to="/" replace />;
  return <>{children}</>;
}
