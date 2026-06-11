import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, FileText, Globe, Lock, Plus, Search, Trash2, Users } from 'lucide-react';
import type { ReactNode } from 'react';
import type { KnowledgeBase, Visibility } from '@/types';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import {
  createKnowledgeBase,
  loadKnowledgeStore,
  saveKnowledgeStore,
} from './mockKnowledgeStore';

export default function KnowledgeListPage() {
  const navigate = useNavigate();
  const [store, setStore] = useState(loadKnowledgeStore);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return store.kbs;
    return store.kbs.filter((kb) =>
      `${kb.name} ${kb.description || ''}`.toLowerCase().includes(q),
    );
  }, [store.kbs, query]);

  function persist(next: typeof store) {
    setStore(next);
    saveKnowledgeStore(next);
  }

  async function removeKb(kb: KnowledgeBase) {
    const ok = await confirm({
      title: '删除知识库',
      message: `确认删除「${kb.name}」? 关联文档也会从静态页面中移除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!ok) return;
    persist({
      kbs: store.kbs.filter((item) => item.id !== kb.id),
      docs: store.docs.filter((doc) => doc.kb_id !== kb.id),
    });
  }

  return (
    <div className="h-full overflow-y-auto bg-white">
      <div className="mx-auto max-w-6xl px-8 py-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">知识库</h1>
            <p className="mt-1 text-sm text-gray-500">
              静态占位页面，可先完成知识库与文档管理流程设计。
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded-md bg-black px-3 py-1.5 text-sm text-white transition-colors hover:bg-gray-800"
          >
            <Plus size={14} />
            新建知识库
          </button>
        </div>

        <div className="mb-5 flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
          <Search size={15} className="text-gray-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索知识库名称或描述"
            className="flex-1 bg-transparent outline-none placeholder:text-gray-400"
          />
        </div>

        {filtered.length === 0 ? (
          <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
            <BookOpen className="mx-auto text-gray-300" size={40} />
            <p className="mt-3 text-sm text-gray-500">没有匹配的知识库</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map((kb) => (
              <KbCard
                key={kb.id}
                kb={kb}
                onOpen={() => navigate(`/knowledge/${kb.id}`)}
                onDelete={() => removeKb(kb)}
              />
            ))}
          </div>
        )}
      </div>

      {creating && (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreate={(input) => {
            const kb = createKnowledgeBase(input);
            const next = { ...store, kbs: [kb, ...store.kbs] };
            persist(next);
            setCreating(false);
            navigate(`/knowledge/${kb.id}`);
          }}
        />
      )}
    </div>
  );
}

function KbCard({
  kb,
  onOpen,
  onDelete,
}: {
  kb: KnowledgeBase;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="group rounded-lg border border-gray-200 bg-white p-4 transition-all hover:border-gray-400 hover:shadow-sm">
      <button onClick={onOpen} className="block w-full text-left">
        <div className="mb-3 flex items-start justify-between">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50">
            <BookOpen className="text-blue-600" size={18} />
          </div>
          <VisibilityBadge visibility={kb.visibility} />
        </div>
        <h3 className="mb-1 truncate text-sm font-medium">{kb.name}</h3>
        <p className="mb-4 line-clamp-2 min-h-[2.5rem] text-xs leading-5 text-gray-500">
          {kb.description || '无描述'}
        </p>
        <div className="flex items-center gap-3 text-[11px] text-gray-400">
          <span className="flex items-center gap-1">
            <FileText size={11} />
            {kb.document_count} 文档
          </span>
          <span>{kb.chunk_count} 分块</span>
          <span>{formatBytes(kb.size_bytes)}</span>
        </div>
      </button>
      <div className="mt-4 flex justify-end border-t pt-3">
        <button
          onClick={onDelete}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-red-50 hover:text-red-600"
        >
          <Trash2 size={12} />
          删除
        </button>
      </div>
    </div>
  );
}

export function VisibilityBadge({ visibility }: { visibility: Visibility }) {
  const config = {
    private: { label: '私有', icon: <Lock size={10} />, cls: 'bg-gray-100 text-gray-600' },
    workspace: { label: '团队', icon: <Users size={10} />, cls: 'bg-blue-50 text-blue-600' },
    public: { label: '公开', icon: <Globe size={10} />, cls: 'bg-green-50 text-green-600' },
  }[visibility];
  return (
    <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]', config.cls)}>
      {config.icon}
      {config.label}
    </span>
  );
}

function CreateDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (input: { name: string; description?: string; visibility: Visibility }) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('private');

  return (
    <div onClick={onClose} className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
        <h2 className="mb-4 text-base font-semibold">新建知识库</h2>
        <div className="space-y-3">
          <Field label="名称 *">
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如: 产品手册"
              className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <Field label="描述">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="选填"
              className="w-full resize-none rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <Field label="可见性">
            <div className="flex gap-2">
              {(['private', 'workspace', 'public'] as Visibility[]).map((v) => (
                <button
                  key={v}
                  onClick={() => setVisibility(v)}
                  className={cn(
                    'flex-1 rounded-md border px-3 py-1.5 text-xs transition-colors',
                    visibility === v
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 hover:bg-gray-50',
                  )}
                >
                  {v === 'private' ? '私有' : v === 'workspace' ? '团队' : '公开'}
                </button>
              ))}
            </div>
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border px-3 py-1.5 text-sm hover:bg-gray-50">
            取消
          </button>
          <button
            onClick={() => onCreate({ name: name.trim(), description: description.trim() || undefined, visibility })}
            disabled={!name.trim()}
            className="rounded-md bg-black px-3 py-1.5 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
          >
            创建
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-gray-600">{label}</span>
      {children}
    </label>
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
