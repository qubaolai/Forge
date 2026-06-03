import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { kbApi } from '@/api';
import { KnowledgeBase, Visibility } from '@/types';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';

interface Props {
  kb: KnowledgeBase;
}

export function KbSettingsTab({ kb }: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState(kb.name);
  const [description, setDescription] = useState(kb.description || '');
  const [visibility, setVisibility] = useState<Visibility>(kb.visibility);

  const dirty = name !== kb.name || description !== (kb.description || '') || visibility !== kb.visibility;

  const updateMutation = useMutation({
    mutationFn: () =>
      kbApi.update(kb.id, {
        name: name.trim(),
        description: description.trim() || undefined,
        visibility,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge-base', kb.id] });
      qc.invalidateQueries({ queryKey: ['knowledge-bases'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => kbApi.remove(kb.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge-bases'] });
      navigate('/knowledge');
    },
  });

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-2xl mx-auto space-y-8">
        {/* 基本信息 */}
        <section>
          <h2 className="text-sm font-medium mb-3">基本信息</h2>
          <div className="space-y-3">
            <Field label="名称">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
              />
            </Field>
            <Field label="描述">
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 resize-none"
              />
            </Field>
            <Field label="可见性">
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
            </Field>
            <div className="flex justify-end">
              <button
                onClick={() => updateMutation.mutate()}
                disabled={!dirty || !name.trim() || updateMutation.isPending}
                className="px-4 py-1.5 text-sm bg-black text-white rounded-md
                  hover:bg-gray-800 disabled:opacity-50"
              >
                {updateMutation.isPending ? '保存中…' : '保存修改'}
              </button>
            </div>
          </div>
        </section>

        {/* 索引配置(只读展示) */}
        <section>
          <h2 className="text-sm font-medium mb-3">索引配置</h2>
          <div className="bg-gray-50 rounded-md p-4 space-y-2 text-xs">
            <KvRow k="分块大小" v={`${kb.chunk_size} tokens`} />
            <KvRow k="分块重叠" v={`${kb.chunk_overlap} tokens`} />
          </div>
          <p className="text-xs text-gray-400 mt-2">
            修改索引配置需重新解析所有文档,暂不支持在此处修改。
          </p>
        </section>

        {/* 危险区 */}
        <section>
          <h2 className="text-sm font-medium text-red-600 mb-3">危险操作</h2>
          <div className="border border-red-200 rounded-md p-4 flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">删除此知识库</div>
              <div className="text-xs text-gray-500 mt-0.5">
                所有文档和索引都会被永久删除,此操作不可撤销。
              </div>
            </div>
            <button
              onClick={async () => {
                if (await confirm({ title: '删除知识库', message: `确认删除「${kb.name}」? 所有文档和索引都会被永久删除，此操作不可撤销。`, confirmLabel: '永久删除', danger: true })) {
                  deleteMutation.mutate();
                }
              }}
              disabled={deleteMutation.isPending}
              className="px-3 py-1.5 text-sm border border-red-500 text-red-600
                rounded-md hover:bg-red-50 disabled:opacity-50"
            >
              {deleteMutation.isPending ? '删除中…' : '删除'}
            </button>
          </div>
        </section>
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

function KvRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-500">{k}</span>
      <span className="font-medium">{v}</span>
    </div>
  );
}
