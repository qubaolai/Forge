/**
 * 模型管理两个页面(模型配置 / 供应商与模型)共用的基础组件、常量与小工具。
 */
import { Brain, Database, Layers, Loader2 } from 'lucide-react';

import type { ProviderModel, SystemModelBinding } from '@/types';
import { cn } from '@/lib/utils';

export const PROVIDERS_KEY = ['admin-providers'];

export const inputCls =
  'w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-orange-400';

export const THINKING_LEVELS = ['low', 'medium', 'high', 'xhigh'] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];

export type ModelTypeFilter = ProviderModel['model_type'] | 'all';
export type ModelStatusFilter = 'enabled' | 'disabled' | 'all';

export type AdminModelItem = ProviderModel & {
  providerName: string;
  providerEnabled: boolean;
  providerBaseUrl: string | null;
  keyCount: number;
};

export const ROLE_ORDER: SystemModelBinding['role'][] = [
  'rag_embedding',
  'semantic_history_embedding',
  'rag_reranker',
];

export function isThinkingLevel(value: string): value is ThinkingLevel {
  return THINKING_LEVELS.includes(value as ThinkingLevel);
}

export function roleLabel(role: SystemModelBinding['role']) {
  return {
    rag_embedding: 'RAG Embedding',
    semantic_history_embedding: '语义历史 Embedding',
    rag_reranker: 'RAG Reranker',
  }[role];
}

export function roleDescription(role: SystemModelBinding['role']) {
  return {
    rag_embedding: '知识库向量入库与向量召回使用的 Embedding 模型',
    semantic_history_embedding: '上下文语义历史捞取使用的 Embedding 模型',
    rag_reranker: 'RAG 检索结果二次排序使用的 Reranker 模型',
  }[role];
}

export function roleIcon(role: SystemModelBinding['role']) {
  if (role === 'rag_embedding') return <Database size={18} />;
  if (role === 'semantic_history_embedding') return <Brain size={18} />;
  return <Layers size={18} />;
}

export function modelTypeLabel(type: ProviderModel['model_type']) {
  return { chat: 'Chat', embedding: 'Embedding', reranker: 'Reranker' }[type];
}

export function modelTypeTone(type: ProviderModel['model_type']) {
  return {
    chat: 'bg-blue-50 text-blue-700 border-blue-100',
    embedding: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    reranker: 'bg-purple-50 text-purple-700 border-purple-100',
  }[type];
}

export function fmtNumber(value: unknown) {
  const num = Number(value || 0);
  return num > 0 ? num.toLocaleString() : '-';
}

export function Switch({
  checked,
  onChange,
  loading,
  size = 'md',
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  loading?: boolean;
  size?: 'sm' | 'md';
}) {
  const w = size === 'sm' ? 'h-4 w-7' : 'h-5 w-9';
  const dot = size === 'sm' ? 'h-3 w-3' : 'h-4 w-4';
  const translate = size === 'sm' ? 'translate-x-3' : 'translate-x-4';
  return (
    <button
      onClick={() => !loading && onChange(!checked)}
      disabled={loading}
      className={cn(
        'relative inline-flex shrink-0 items-center rounded-full transition-colors',
        w,
        checked ? 'bg-orange-500' : 'bg-gray-300',
        loading && 'opacity-60',
      )}
      title={checked ? '已启用' : '已禁用'}
    >
      <span
        className={cn(
          'absolute left-0.5 inline-flex items-center justify-center rounded-full bg-white transition-transform',
          dot,
          checked && translate,
        )}
      >
        {loading && <Loader2 size={size === 'sm' ? 8 : 10} className="animate-spin text-gray-400" />}
      </span>
    </button>
  );
}

export function CapToggle({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded-md border px-2.5 py-1 text-xs',
        on ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 hover:bg-gray-50',
      )}
    >
      {label}
    </button>
  );
}

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div onClick={onClose} className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 className="mb-4 text-base font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function DialogActions({
  onClose,
  onSave,
  saving,
  disabled,
}: {
  onClose: () => void;
  onSave: () => void;
  saving: boolean;
  disabled: boolean;
}) {
  return (
    <div className="mt-5 flex justify-end gap-2">
      <button onClick={onClose} className="rounded-md border px-3 py-1.5 text-sm hover:bg-gray-50">
        取消
      </button>
      <button
        onClick={onSave}
        disabled={disabled || saving}
        className="rounded-md bg-orange-500 px-3 py-1.5 text-sm text-white hover:bg-orange-600 disabled:opacity-50"
      >
        {saving ? '保存中…' : '保存'}
      </button>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-gray-600">{label}</label>
      {children}
    </div>
  );
}
