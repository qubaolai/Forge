import { useEffect, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { kbApi } from '@/api';
import { Citation, KnowledgeChunkFullText } from '@/types';
import { cn } from '@/lib/utils';

interface Props {
  citations: Citation[];
  highlightedIndex: number | null;
  onClose: () => void;
}

function formatCitationMetadata(metadata?: Record<string, unknown>): string[] {
  if (!metadata) return [];
  const page = typeof metadata.page === 'number' ? metadata.page : null;
  const start = typeof metadata.page_start === 'number' ? metadata.page_start : page;
  const end = typeof metadata.page_end === 'number' ? metadata.page_end : start;
  const items: string[] = [];
  if (start != null) {
    items.push(end != null && end !== start ? `页码: ${start}-${end}` : `页码: ${start}`);
  }
  for (const [key, value] of Object.entries(metadata)) {
    if (['page', 'page_start', 'page_end'].includes(key)) continue;
    if (value == null || value === '') continue;
    items.push(`${key}: ${value}`);
  }
  return items;
}

export function CitationPanel({ citations, highlightedIndex, onClose }: Props) {
  const refs = useRef<Map<number, HTMLDivElement | null>>(new Map());
  const [openChunkId, setOpenChunkId] = useState<string | null>(null);
  const [fullTextByChunk, setFullTextByChunk] = useState<Record<string, KnowledgeChunkFullText>>({});
  const [loadingChunkId, setLoadingChunkId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState('');
  const citationKey = citations.map((c) => `${c.index}:${c.chunk_id}`).join('|');

  useEffect(() => {
    if (highlightedIndex !== null) {
      const el = refs.current.get(highlightedIndex);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightedIndex]);

  useEffect(() => {
    setOpenChunkId(null);
    setLoadingChunkId(null);
    setLoadError('');
  }, [citationKey]);

  async function toggleFullText(chunkId: string) {
    setLoadError('');
    if (openChunkId === chunkId) {
      setOpenChunkId(null);
      return;
    }
    setOpenChunkId(chunkId);
    if (fullTextByChunk[chunkId]) return;
    setLoadingChunkId(chunkId);
    try {
      const fullText = await kbApi.getChunkFullText(chunkId);
      setFullTextByChunk((prev) => ({ ...prev, [chunkId]: fullText }));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '加载原文失败');
    } finally {
      setLoadingChunkId(null);
    }
  }

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
          citations.map((c) => {
            const metadataItems = formatCitationMetadata(c.metadata);
            return (
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
                {metadataItems.length > 0 && (
                  <div className="mt-1.5 text-[10px] text-gray-400">
                    {metadataItems.join(' · ')}
                  </div>
                )}
                <div className="mt-2 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => toggleFullText(c.chunk_id)}
                    className="text-[11px] text-blue-600 hover:text-blue-800"
                  >
                    {openChunkId === c.chunk_id ? '收起原文' : '查看原文'}
                  </button>
                  {loadingChunkId === c.chunk_id && (
                    <Loader2 size={12} className="animate-spin text-gray-400" />
                  )}
                </div>
                {openChunkId === c.chunk_id && fullTextByChunk[c.chunk_id] && (
                  <div className="mt-2 rounded border border-gray-200 bg-white p-2">
                    <div className="mb-1 text-[11px] font-medium text-gray-600">
                      原始父块全文
                    </div>
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-gray-700">
                      {fullTextByChunk[c.chunk_id].content}
                    </pre>
                  </div>
                )}
                {openChunkId === c.chunk_id && loadError && (
                  <div className="mt-2 text-[11px] text-red-600">{loadError}</div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
