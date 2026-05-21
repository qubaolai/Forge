import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, BookOpen, FileText, Globe, Lock, Users as UsersIcon } from 'lucide-react';
import { kbApi } from '@/api';
import { KnowledgeBase, Visibility } from '@/types';
import { cn } from '@/lib/utils';

export default function KnowledgeListPage() {
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: () => kbApi.list({ page: 1, page_size: 50 }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">知识库</h1>
            <p className="text-sm text-gray-500 mt-1">
              管理用于 RAG 检索的文档集合
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm
              bg-black text-white rounded-md hover:bg-gray-800 transition-colors"
          >
            <Plus size={14} />
            新建知识库
          </button>
        </div>

        {isLoading ? (
          <div className="text-sm text-gray-400 py-12 text-center">加载中…</div>
        ) : !data || data.items.length === 0 ? (
          <EmptyState onCreate={() => setCreating(true)} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.items.map((kb) => (
              <KbCard key={kb.id} kb={kb} />
            ))}
          </div>
        )}
      </div>

      {creating && <CreateDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="border-2 border-dashed border-gray-200 rounded-lg py-16 text-center">
      <BookOpen className="mx-auto text-gray-300" size={40} />
      <p className="mt-3 text-sm text-gray-500">还没有知识库</p>
      <button
        onClick={onCreate}
        className="mt-4 text-sm text-blue-600 hover:underline"
      >
        创建第一个知识库
      </button>
    </div>
  );
}

function KbCard({ kb }: { kb: KnowledgeBase }) {
  const navigate = useNavigate();
  return (
    <div
      onClick={() => navigate(`/knowledge/${kb.id}`)}
      className="group border border-gray-200 rounded-lg p-4 cursor-pointer
        hover:border-gray-400 hover:shadow-sm transition-all bg-white"
    >
      <div className="flex items-start justify-between mb-2">
        <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center">
          <BookOpen className="text-blue-600" size={18} />
        </div>
        <VisibilityBadge visibility={kb.visibility} />
      </div>
      <h3 className="font-medium text-sm mb-1 truncate">{kb.name}</h3>
      <p className="text-xs text-gray-500 mb-3 line-clamp-2 min-h-[2rem]">
        {kb.description || '无描述'}
      </p>
      <div className="flex items-center gap-3 text-[11px] text-gray-400">
        <span className="flex items-center gap-1">
          <FileText size={11} /> {kb.document_count} 文档
        </span>
        <span>·</span>
        <span>{kb.chunk_count} 分块</span>
      </div>
    </div>
  );
}

export function VisibilityBadge({ visibility }: { visibility: Visibility }) {
  const config = {
    private: { label: '私有', icon: <Lock size={10} />, cls: 'bg-gray-100 text-gray-600' },
    workspace: { label: '团队', icon: <UsersIcon size={10} />, cls: 'bg-blue-50 text-blue-600' },
    public: { label: '公开', icon: <Globe size={10} />, cls: 'bg-green-50 text-green-600' },
  }[visibility];
  return (
    <span className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px]', config.cls)}>
      {config.icon}
      {config.label}
    </span>
  );
}

function CreateDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('private');

  const mutation = useMutation({
    mutationFn: () =>
      kbApi.create({ name: name.trim(), description: description.trim() || undefined, visibility }),
    onSuccess: (kb) => {
      qc.invalidateQueries({ queryKey: ['knowledge-bases'] });
      onClose();
      navigate(`/knowledge/${kb.id}`);
    },
  });

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-lg shadow-xl w-full max-w-md p-5"
      >
        <h2 className="text-base font-semibold mb-4">新建知识库</h2>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">名称 *</label>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如:产品手册"
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="选填"
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">可见性</label>
            <div className="flex gap-2">
              {(['private', 'workspace', 'public'] as Visibility[]).map((v) => (
                <button
                  key={v}
                  onClick={() => setVisibility(v)}
                  className={cn(
                    'flex-1 px-3 py-1.5 text-xs rounded-md border transition-colors',
                    visibility === v
                      ? 'border-gray-900 bg-gray-900 text-white'
                      : 'border-gray-200 hover:bg-gray-50',
                  )}
                >
                  {v === 'private' ? '私有' : v === 'workspace' ? '团队' : '公开'}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50"
          >
            取消
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={!name.trim() || mutation.isPending}
            className="px-3 py-1.5 text-sm bg-black text-white rounded-md
              hover:bg-gray-800 disabled:opacity-50"
          >
            {mutation.isPending ? '创建中…' : '创建'}
          </button>
        </div>
      </div>
    </div>
  );
}
