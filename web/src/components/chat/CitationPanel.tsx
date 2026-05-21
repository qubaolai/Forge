import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { Citation } from '@/types';
import { cn } from '@/lib/utils';

interface Props {
  citations: Citation[];
  highlightedIndex: number | null;
  onClose: () => void;
}

export function CitationPanel({ citations, highlightedIndex, onClose }: Props) {
  const refs = useRef<Map<number, HTMLDivElement | null>>(new Map());

  useEffect(() => {
    if (highlightedIndex !== null) {
      const el = refs.current.get(highlightedIndex);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightedIndex]);

  return (
    <aside className="w-[320px] border-l bg-white flex flex-col h-full shrink-0">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="text-sm font-medium">引用来源 ({citations.length})</div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
          <X size={16} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
        {citations.length === 0 ? (
          <div className="text-xs text-gray-400 text-center mt-8">暂无引用</div>
        ) : (
          citations.map((c) => (
            <div
              key={c.chunk_id}
              ref={(el) => {
                refs.current.set(c.index, el);
              }}
              className={cn(
                'border rounded-md p-3 transition-colors',
                highlightedIndex === c.index
                  ? 'border-blue-400 bg-blue-50'
                  : 'border-gray-200 bg-gray-50',
              )}
            >
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-blue-100 text-blue-700 text-[10px]">
                    {c.index}
                  </span>
                  <span className="truncate max-w-[180px]" title={c.document_name}>
                    {c.document_name}
                  </span>
                </div>
                <span className="text-[10px] text-gray-400 tabular-nums">
                  {c.score.toFixed(2)}
                </span>
              </div>
              <div className="text-xs text-gray-600 leading-relaxed line-clamp-4">
                {c.content}
              </div>
              {c.metadata && Object.keys(c.metadata).length > 0 && (
                <div className="mt-1.5 text-[10px] text-gray-400">
                  {Object.entries(c.metadata)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(' · ')}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
