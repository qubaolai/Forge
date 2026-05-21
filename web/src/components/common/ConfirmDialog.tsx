import { create } from 'zustand';
import * as Dialog from '@radix-ui/react-dialog';

interface ConfirmOpts {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

interface DialogStore {
  open: boolean;
  opts: ConfirmOpts;
  resolve: ((value: boolean) => void) | null;
  _show: (opts: ConfirmOpts, resolve: (value: boolean) => void) => void;
  _respond: (value: boolean) => void;
}

const useDialogStore = create<DialogStore>((set, get) => ({
  open: false,
  opts: { message: '' },
  resolve: null,
  _show: (opts, resolve) => set({ open: true, opts, resolve }),
  _respond: (value) => {
    get().resolve?.(value);
    set({ open: false, resolve: null });
  },
}));

/** 全局确认对话框，用法同 window.confirm 但返回 Promise */
export function confirm(opts: ConfirmOpts | string): Promise<boolean> {
  const normalized = typeof opts === 'string' ? { message: opts } : opts;
  return new Promise((resolve) => {
    useDialogStore.getState()._show(normalized, resolve);
  });
}

/** 挂在 App 根部，渲染实际弹窗 */
export function ConfirmDialog() {
  const { open, opts, _respond } = useDialogStore();

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) _respond(false);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl bg-white p-6 shadow-xl data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95">
          {opts.title && (
            <Dialog.Title className="mb-2 text-base font-semibold text-gray-900">
              {opts.title}
            </Dialog.Title>
          )}
          <Dialog.Description className="text-sm text-gray-600 leading-relaxed">
            {opts.message}
          </Dialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => _respond(false)}
              className="px-4 py-1.5 text-sm rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            >
              {opts.cancelLabel || '取消'}
            </button>
            <button
              onClick={() => _respond(true)}
              className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
                opts.danger
                  ? 'bg-red-500 text-white hover:bg-red-600'
                  : 'bg-black text-white hover:bg-gray-800'
              }`}
            >
              {opts.confirmLabel || '确认'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
