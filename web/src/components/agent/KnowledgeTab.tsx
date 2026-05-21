import { useQuery } from '@tanstack/react-query';
import { Check, BookOpen } from 'lucide-react';
import { kbApi } from '@/api';
import { AgentDraft } from './draft';
import { Field, Slider, Toggle } from './FormControls';
import { cn } from '@/lib/utils';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

export function KnowledgeTab({ draft, update }: Props) {
  const { data: kbs, isLoading } = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: () => kbApi.list({ page: 1, page_size: 100 }),
  });

  const selectedSet = new Set(draft.retrieval.kb_ids);

  function patchRetrieval(p: Partial<AgentDraft['retrieval']>) {
    update({ retrieval: { ...draft.retrieval, ...p } });
  }

  function toggleKb(kbId: string) {
    const next = selectedSet.has(kbId)
      ? draft.retrieval.kb_ids.filter((id) => id !== kbId)
      : [...draft.retrieval.kb_ids, kbId];
    patchRetrieval({ kb_ids: next });
  }

  return (
    <div className="max-w-xl">
      <Field
        label="关联知识库"
        hint={`已选 ${draft.retrieval.kb_ids.length} 个`}
      >
        {isLoading ? (
          <div className="text-sm text-gray-400">加载中…</div>
        ) : !kbs || kbs.items.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-md p-3">
            还没有知识库,先去创建一个吧。
          </div>
        ) : (
          <div className="border rounded-md divide-y max-h-64 overflow-y-auto">
            {kbs.items.map((kb) => {
              const selected = selectedSet.has(kb.id);
              return (
                <div
                  key={kb.id}
                  onClick={() => toggleKb(kb.id)}
                  className={cn(
                    'flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors',
                    selected ? 'bg-blue-50/50' : 'hover:bg-gray-50',
                  )}
                >
                  <div className={cn(
                    'w-4 h-4 rounded border flex items-center justify-center shrink-0',
                    selected ? 'bg-blue-600 border-blue-600' : 'border-gray-300',
                  )}>
                    {selected && <Check size={12} className="text-white" />}
                  </div>
                  <BookOpen size={14} className="text-gray-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{kb.name}</div>
                    <div className="text-[11px] text-gray-400">
                      {kb.document_count} 文档 · {kb.chunk_count} 分块
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Field>

      <Field label={`Top-K: ${draft.retrieval.top_k}`} hint="每次检索返回的最多片段数">
        <Slider
          value={draft.retrieval.top_k}
          min={1} max={20}
          onChange={(v) => patchRetrieval({ top_k: v })}
        />
      </Field>

      <Field
        label={`相似度阈值: ${draft.retrieval.score_threshold.toFixed(2)}`}
        hint="低于此分数的片段不会被使用"
      >
        <Slider
          value={draft.retrieval.score_threshold}
          min={0} max={1} step={0.05}
          onChange={(v) => patchRetrieval({ score_threshold: v })}
        />
      </Field>

      <div className="flex flex-col gap-2 mt-4">
        <Toggle
          checked={draft.retrieval.hybrid}
          onChange={(v) => patchRetrieval({ hybrid: v })}
          label="混合检索 (BM25 + 向量)"
        />
        <Toggle
          checked={draft.retrieval.rerank}
          onChange={(v) => patchRetrieval({ rerank: v })}
          label="启用重排模型"
        />
      </div>
    </div>
  );
}
