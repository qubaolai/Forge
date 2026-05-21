import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Search, FileText } from 'lucide-react';
import { kbApi } from '@/api';
import { RetrievalResult } from '@/types';

interface Props {
  kbId: string;
}

export function RetrievalTab({ kbId }: Props) {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(4);
  const [threshold, setThreshold] = useState(0.5);
  const [hybrid, setHybrid] = useState(true);
  const [rerank, setRerank] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      kbApi.retrieve(kbId, { query, top_k: topK, score_threshold: threshold, hybrid, rerank }),
  });

  const results = (mutation.data ?? []) as RetrievalResult[];

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-4xl mx-auto">
        <div className="mb-4">
          <label className="block text-sm font-medium mb-2">检索查询</label>
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && query.trim()) mutation.mutate();
              }}
              placeholder="输入想要测试的问题…"
              className="flex-1 border rounded-md px-3 py-2 text-sm outline-none focus:border-gray-400"
            />
            <button
              onClick={() => mutation.mutate()}
              disabled={!query.trim() || mutation.isPending}
              className="flex items-center gap-1.5 px-4 py-2 text-sm
                bg-black text-white rounded-md hover:bg-gray-800 disabled:opacity-50"
            >
              <Search size={14} />
              {mutation.isPending ? '检索中…' : '检索'}
            </button>
          </div>
        </div>

        {/* 参数配置 */}
        <div className="bg-gray-50 rounded-md p-4 mb-6 grid grid-cols-2 gap-4">
          <NumField label={`Top-K: ${topK}`} type="range" min={1} max={20} value={topK} onChange={setTopK} />
          <NumField
            label={`相似度阈值: ${threshold.toFixed(2)}`}
            type="range" min={0} max={1} step={0.05} value={threshold} onChange={setThreshold}
          />
          <Toggle label="混合检索 (Hybrid)" value={hybrid} onChange={setHybrid} />
          <Toggle label="重排 (Rerank)" value={rerank} onChange={setRerank} />
        </div>

        {/* 结果 */}
        {mutation.isError && (
          <div className="text-sm text-red-600">{(mutation.error as Error).message}</div>
        )}
        {results.length > 0 && (
          <div>
            <h3 className="text-sm font-medium mb-3">检索结果 ({results.length})</h3>
            <div className="flex flex-col gap-3">
              {results.map((r, i) => (
                <ResultCard key={r.chunk.id} result={r} rank={i + 1} />
              ))}
            </div>
          </div>
        )}
        {mutation.isSuccess && results.length === 0 && (
          <div className="text-sm text-gray-400 py-8 text-center">没有匹配的结果</div>
        )}
      </div>
    </div>
  );
}

function ResultCard({ result, rank }: { result: RetrievalResult; rank: number }) {
  return (
    <div className="border rounded-md p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded bg-blue-100 text-blue-700 font-medium">
            {rank}
          </span>
          <FileText size={12} className="text-gray-400" />
          <span className="font-medium truncate">{result.document.name}</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-400">分数</span>
          <span className="font-mono font-medium tabular-nums">{result.score.toFixed(3)}</span>
          <ScoreBar score={result.score} />
        </div>
      </div>
      <p className="text-sm text-gray-700 leading-relaxed">{result.chunk.content}</p>
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  return (
    <div className="w-16 h-1.5 bg-gray-200 rounded overflow-hidden">
      <div
        className="h-full bg-gradient-to-r from-blue-400 to-blue-600 transition-all"
        style={{ width: `${Math.min(100, score * 100)}%` }}
      />
    </div>
  );
}

function NumField({
  label, value, onChange, type = 'number', min, max, step,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  type?: 'number' | 'range';
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
    </div>
  );
}

function Toggle({
  label, value, onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="rounded"
      />
      <span className="text-xs text-gray-700">{label}</span>
    </label>
  );
}
