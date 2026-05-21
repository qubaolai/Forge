import { create } from 'zustand';
import { useEffect } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';
import { cn } from '@/lib/utils';

type ToastType = 'success' | 'error' | 'info';

interface Toast {
  id: string;
  type: ToastType;
  message: string;
}

interface ToastStore {
  toasts: Toast[];
  push: (type: ToastType, message: string) => void;
  remove: (id: string) => void;
}

const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (type, message) => {
    const id = String(Date.now()) + Math.random();
    set((s) => ({ toasts: [...s.toasts, { id, type, message }] }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 3500);
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** 全局调用入口 */
export const toast = {
  success: (msg: string) => useToastStore.getState().push('success', msg),
  error: (msg: string) => useToastStore.getState().push('error', msg),
  info: (msg: string) => useToastStore.getState().push('info', msg),
};

/** 容器组件,挂在 App 根部 */
export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.remove);

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => <ToastItem key={t.id} toast={t} onClose={() => remove(t.id)} />)}
    </div>
  );
}

function ToastItem({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const icon = {
    success: <CheckCircle2 size={16} className="text-green-600" />,
    error: <AlertCircle size={16} className="text-red-600" />,
    info: <Info size={16} className="text-blue-600" />,
  }[toast.type];

  // 入场微动画
  useEffect(() => {
    /* placeholder for fade-in if needed */
  }, []);

  return (
    <div className={cn(
      'pointer-events-auto flex items-center gap-2 min-w-[260px] max-w-[400px]',
      'bg-white border rounded-md shadow-md px-3 py-2.5',
      'animate-in slide-in-from-right-2 fade-in',
    )}>
      {icon}
      <span className="text-sm flex-1">{toast.message}</span>
      <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
        <X size={14} />
      </button>
    </div>
  );
}
