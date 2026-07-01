import { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Database,
  Eye,
  FilePlus2,
  FileText,
  Layers,
  RefreshCw,
  Save,
  Search,
  Settings,
  Trash2,
  Upload,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type {
  KbDocumentChunkInfo,
  KbRetrievalTrace,
  KnowledgeBase,
  KnowledgeDocument,
  Visibility,
} from '@/types';
import { ApiError } from '@/types';
import { kbApi } from '@/api';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import { toast } from '@/components/common/Toast';
import { formatBytes, VisibilityBadge } from './KnowledgeListPage';

type TabKey = 'documents' | 'retrieval' | 'settings';

// 入库未到终态的状态: 文档列表轮询期间据此判断是否继续刷新
const DOC_ACTIVE_STATUSES = new Set(['pending', 'parsing', 'chunking', 'embedding']);

function formatHitPageRange(hit: {
  page?: number | null;
  page_start?: number | null;
  page_end?: number | null;
}): string {
  const start = hit.page_start ?? hit.page ?? null;
  const end = hit.page_end ?? start;
  if (start == null) return '';
  if (end == null || end === start) return `第 ${start} 页`;
  return `第 ${start}-${end} 页`;
}

export default function KnowledgeDetailPage() {
  const { kbId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [tab, setTab] = useState<TabKey>('documents');
  const [chunkDoc, setChunkDoc] = useState<KnowledgeDocument | null>(null);

  const { data: kb, isLoading, isError } = useQuery({
    queryKey: ['kb', kbId],
    queryFn: () => kbApi.get(kbId),
    enabled: !!kbId,
  });

  const { data: docsData } = useQuery({
    queryKey: ['kb-docs', kbId],
    queryFn: () => kbApi.listDocuments(kbId, { page: 1, page_size: 200 }),
    enabled: !!kbId,
    // 有文档处于入库中 / 向量重建中时每 2s 轮询, 全部到终态后停止
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? [];
      const active = items.some(
        (d) => DOC_ACTIVE_STATUSES.has(d.status) || d.vector_index_status === 'rebuilding',
      );
      return active ? 2000 : false;
    },
  });
  const docs = docsData?.items ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['kb-docs', kbId] });
    queryClient.invalidateQueries({ queryKey: ['kb', kbId] });
  };

  const uploadMutation = useMutation({
    mutationFn: (file: File) => kbApi.uploadDocument(kbId, file),
    onSuccess: () => {
      toast.success('已上传，正在后台解析入库');
      invalidate();
    },
    onError: (e) => toast.error((e as ApiError).message || '上传失败'),
  });

  const removeDocMutation = useMutation({
    mutationFn: (docId: string) => kbApi.removeDocument(kbId, docId),
    onSuccess: () => {
      toast.success('已删除');
      invalidate();
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });

  const rebuildMutation = useMutation({
    mutationFn: (docId: string) => kbApi.rebuildDocument(kbId, docId),
    onSuccess: () => {
      toast.success('已触发向量重建');
      invalidate();
    },
    onError: (e) => toast.error((e as ApiError).message || '重建失败'),
  });

  const reingestMutation = useMutation({
    mutationFn: (docId: string) => kbApi.reingestDocument(kbId, docId),
    onSuccess: () => {
      toast.success('已触发重新入库');
      invalidate();
    },
    onError: (e) => toast.error((e as ApiError).message || '重新入库失败'),
  });

  const removeKbMutation = useMutation({
    mutationFn: () => kbApi.remove(kbId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-list'] });
      toast.success('知识库已删除');
      navigate('/knowledge');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });

  if (isLoading) {
    return <div className="flex h-full items-center justify-center bg-white text-sm text-gray-400">加载中…</div>;
  }
  if (isError || !kb) {
    return (
      <div className="flex h-full items-center justify-center bg-white">
        <div className="text-center">
          <Database className="mx-auto mb-3 text-gray-300" size={42} />
          <h1 className="text-base font-semibold">知识库不存在</h1>
          <p className="mt-1 text-sm text-gray-500">可能已被删除，或你没有访问权限。</p>
          <button
            onClick={() => navigate('/knowledge')}
            className="mt-4 rounded-md border px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            返回知识库列表
          </button>
        </div>
      </div>
    );
  }

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    Array.from(files).forEach((file) => uploadMutation.mutate(file));
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function removeDoc(doc: KnowledgeDocument) {
    const ok = await confirm({
      title: '删除文档',
      message: `确认删除「${doc.name}」? 该文档的索引数据会一并清除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (ok) removeDocMutation.mutate(doc.id);
  }

  async function reingestDoc(doc: KnowledgeDocument) {
    const ok = await confirm({
      title: '重新入库文档',
      message: `确认重新入库「${doc.name}」? 将按当前知识库分块配置重新解析、切分并重建全文索引。`,
      confirmLabel: '重新入库',
    });
    if (ok) reingestMutation.mutate(doc.id);
  }

  async function removeKb() {
    const ok = await confirm({
      title: '删除知识库',
      message: `确认删除「${kb!.name}」? 知识库下所有文档与索引都会被清除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (ok) removeKbMutation.mutate();
  }

  return (
    <div className="h-full overflow-y-auto bg-white">
      <div className="mx-auto max-w-6xl px-8 py-7">
        <button
          onClick={() => navigate('/knowledge')}
          className="mb-5 flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900"
        >
          <ArrowLeft size={15} />
          返回知识库
        </button>

        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <h1 className="text-xl font-semibold">{kb.name}</h1>
              <VisibilityBadge visibility={kb.visibility} />
            </div>
            <p className="max-w-2xl text-sm leading-6 text-gray-500">
              {kb.description || '暂无描述。可在设置中补充知识库用途、范围和维护说明。'}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <Stat label="文档" value={kb.document_count} />
            <Stat label="分块" value={kb.chunk_count} />
            <Stat label="容量" value={formatBytes(kb.size_bytes)} />
          </div>
        </div>

        <div className="mb-6 flex border-b">
          <TabButton active={tab === 'documents'} icon={<FileText size={15} />} onClick={() => setTab('documents')}>
            文档
          </TabButton>
          <TabButton active={tab === 'retrieval'} icon={<Search size={15} />} onClick={() => setTab('retrieval')}>
            检索测试
          </TabButton>
          <TabButton active={tab === 'settings'} icon={<Settings size={15} />} onClick={() => setTab('settings')}>
            设置
          </TabButton>
        </div>

        {tab === 'documents' && (
          <div className="grid gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
            <section>
              <h2 className="mb-3 text-sm font-medium">上传文档</h2>
              <div className="space-y-3 rounded-lg border border-gray-200 p-4 text-sm leading-6 text-gray-600">
                <p>支持 PDF / Word / Markdown / 文本等格式。</p>
                <p>上传后自动进入解析 → 分块 → 向量化流水线，下方列表实时展示入库状态。</p>
                <p>入库失败会标红并给出原因；模型切换后可对单篇文档重建向量索引。</p>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => handleFiles(event.target.files)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadMutation.isPending}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-md bg-black px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
              >
                <Upload size={15} />
                {uploadMutation.isPending ? '上传中…' : '上传文档'}
              </button>
            </section>

            <section>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium">文档列表</h2>
                <span className="text-xs text-gray-400">{docs.length} 个文件</span>
              </div>
              {docs.length === 0 ? (
                <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
                  <FilePlus2 className="mx-auto text-gray-300" size={38} />
                  <p className="mt-3 text-sm text-gray-500">还没有文档</p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-gray-200">
                  {docs.map((doc) => (
                    <DocumentRow
                      key={doc.id}
                      doc={doc}
                      rebuilding={rebuildMutation.isPending}
                      reingesting={reingestMutation.isPending}
                      onViewChunks={() => setChunkDoc(doc)}
                      onDelete={() => removeDoc(doc)}
                      onRebuild={() => rebuildMutation.mutate(doc.id)}
                      onReingest={() => reingestDoc(doc)}
                    />
                  ))}
                </div>
              )}
              {chunkDoc && (
                <DocumentChunksPanel
                  key={chunkDoc.id}
                  kbId={kbId}
                  doc={chunkDoc}
                  onClose={() => setChunkDoc(null)}
                />
              )}
            </section>
          </div>
        )}

        {tab === 'retrieval' && <RetrievalTab kbId={kbId} />}

        {tab === 'settings' && (
          <SettingsTab kb={kb} onDeleteKb={removeKb} deleting={removeKbMutation.isPending} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 检索测试 Tab
// ---------------------------------------------------------------------------
function RetrievalTab({ kbId }: { kbId: string }) {
  const [query, setQuery] = useState('');
  const [topN, setTopN] = useState(5);
  const [showTrace, setShowTrace] = useState(false);

  const searchMutation = useMutation({
    mutationFn: () => kbApi.search(kbId, { query: query.trim(), top_n: topN, debug: showTrace }),
    onError: (e) => toast.error((e as ApiError).message || '检索失败'),
  });
  const results = searchMutation.data?.items ?? [];
  const trace = searchMutation.data?.trace ?? null;

  return (
    <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
      <section className="space-y-4">
        <h2 className="text-sm font-medium">检索配置</h2>
        <Field label="查询内容">
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            rows={4}
            placeholder="输入一个问题，验证知识库召回效果"
            className="w-full resize-none rounded-md border px-3 py-2 text-sm outline-none focus:border-gray-400"
          />
        </Field>
        <Field label="返回片段数 (Top N)">
          <input
            type="number"
            min={1}
            max={20}
            value={topN}
            onChange={(event) => setTopN(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
            className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
          />
        </Field>
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={showTrace}
            onChange={(event) => setShowTrace(event.target.checked)}
          />
          显示检索过程
        </label>
        <button
          onClick={() => searchMutation.mutate()}
          disabled={!query.trim() || searchMutation.isPending}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-black px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
        >
          <Search size={15} />
          {searchMutation.isPending ? '检索中…' : '运行检索'}
        </button>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium">召回结果</h2>
          {searchMutation.isSuccess && <span className="text-xs text-gray-400">{results.length} 条</span>}
        </div>
        {!searchMutation.isSuccess ? (
          <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
            <Search className="mx-auto text-gray-300" size={38} />
            <p className="mt-3 text-sm text-gray-500">运行检索后显示真实召回片段</p>
          </div>
        ) : results.length === 0 ? (
          <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
            <p className="text-sm text-gray-500">没有召回到相关片段</p>
          </div>
        ) : (
          <div className="space-y-3">
            {results.map((hit, idx) => {
              const pageLabel = formatHitPageRange(hit);
              return (
                <div key={`${hit.chunk_id}-${idx}`} className="rounded-lg border border-gray-200 p-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="truncate text-sm font-medium">{hit.document_name || '未命名文档'}</span>
                    <span className="shrink-0 rounded bg-green-50 px-2 py-0.5 text-xs text-green-700">
                      {hit.score.toFixed(4)}
                    </span>
                  </div>
                  {(hit.header_path || pageLabel) && (
                    <p className="mb-1 text-xs text-gray-400">
                      {hit.header_path}
                      {hit.header_path && pageLabel ? ' · ' : ''}
                      {pageLabel}
                    </p>
                  )}
                  <p className="whitespace-pre-wrap text-sm leading-6 text-gray-600">{hit.content}</p>
                </div>
              );
            })}
          </div>
        )}
        {showTrace && searchMutation.isSuccess && <RetrievalTracePanel trace={trace} />}
      </section>
    </div>
  );
}

function DocumentChunksPanel({
  kbId,
  doc,
  onClose,
}: {
  kbId: string;
  doc: KnowledgeDocument;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const pageSize = 20;
  const chunksQuery = useQuery({
    queryKey: ['kb-doc-chunks', kbId, doc.id, page],
    queryFn: () => kbApi.listDocumentChunks(kbId, doc.id, { page, page_size: pageSize }),
    enabled: doc.status === 'indexed',
  });
  const fullTextQuery = useQuery({
    queryKey: ['kb-chunk-full-text', selectedChunkId],
    queryFn: () => kbApi.getChunkFullText(selectedChunkId || ''),
    enabled: !!selectedChunkId,
  });
  const chunks = chunksQuery.data?.items ?? [];
  const total = chunksQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium">分块预览：{doc.name}</h3>
          <p className="mt-0.5 text-xs text-gray-400">仅展示 parent chunk；全文通过“查看全文”读取。</p>
        </div>
        <button onClick={onClose} className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-white">
          关闭
        </button>
      </div>

      {chunksQuery.isLoading ? (
        <p className="py-6 text-center text-sm text-gray-400">加载分块中…</p>
      ) : chunks.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-500">暂无可查看的分块。</p>
      ) : (
        <div className="space-y-3">
          {chunks.map((chunk) => (
            <ChunkDebugCard
              key={chunk.chunk_id}
              chunk={chunk}
              selected={selectedChunkId === chunk.chunk_id}
              onViewFullText={() => setSelectedChunkId(chunk.chunk_id)}
            />
          ))}
        </div>
      )}

      {total > pageSize && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-gray-500">
          <span>
            第 {page} / {totalPages} 页
          </span>
          <button
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            disabled={page <= 1}
            className="rounded border bg-white px-2 py-1 disabled:opacity-40"
          >
            上一页
          </button>
          <button
            onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
            disabled={page >= totalPages}
            className="rounded border bg-white px-2 py-1 disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}

      {selectedChunkId && (
        <div className="mt-4 rounded-md border border-gray-200 bg-white p-3">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-xs font-medium text-gray-700">Parent 原始全文</h4>
            <button
              onClick={() => setSelectedChunkId(null)}
              className="rounded px-2 py-0.5 text-xs text-gray-400 hover:bg-gray-50"
            >
              收起
            </button>
          </div>
          {fullTextQuery.isLoading ? (
            <p className="text-sm text-gray-400">加载全文中…</p>
          ) : (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-3 text-xs leading-5 text-gray-700">
              {fullTextQuery.data?.content || '未读取到内容'}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function ChunkDebugCard({
  chunk,
  selected,
  onViewFullText,
}: {
  chunk: KbDocumentChunkInfo;
  selected: boolean;
  onViewFullText: () => void;
}) {
  const pageLabel = formatHitPageRange(chunk);
  const hasChildDebug = chunk.child_debug_manifest.length > 0;
  return (
    <div className="rounded-md border border-gray-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            #{chunk.seq} {chunk.header_path || '(无标题路径)'}
          </p>
          <p className="mt-0.5 text-xs text-gray-400">
            {chunk.source_type} / {chunk.token_count} tokens / {chunk.content_chars} chars
            {pageLabel ? ` / ${pageLabel}` : ''}
          </p>
        </div>
        <button
          onClick={onViewFullText}
          className={cn(
            'flex items-center gap-1 rounded px-2 py-1 text-xs',
            selected ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-50',
          )}
        >
          <Eye size={12} />
          查看全文
        </button>
      </div>
      <p className="whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs leading-5 text-gray-700">
        {chunk.content_preview}
      </p>
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-medium text-gray-600">Child 分块摘要</span>
          <span className="text-xs text-gray-400">{chunk.child_count} 个 child</span>
        </div>
        {!hasChildDebug ? (
          <p className="rounded bg-amber-50 px-2 py-1.5 text-xs text-amber-700">
            旧文档未记录 child 调试摘要，重新入库后可查看 child 分块详情。
          </p>
        ) : (
          <div className="space-y-1.5">
            {chunk.child_debug_manifest.map((child) => (
              <div key={child.id} className="rounded border border-gray-100 p-2">
                <p className="mb-1 text-[11px] text-gray-400">
                  {child.id} / {child.source_type} / {child.chars} chars
                  {child.splitter ? ` / ${child.splitter}` : ''}
                  {child.row_start != null && child.row_end != null
                    ? ` / 行 ${child.row_start}-${child.row_end}`
                    : ''}
                  {child.table_index != null ? ` / 表 ${child.table_index + 1}` : ''}
                </p>
                <p className="whitespace-pre-wrap text-xs leading-5 text-gray-600">{child.content_preview}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RetrievalTracePanel({ trace }: { trace: KbRetrievalTrace | null }) {
  if (!trace) {
    return (
      <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50 p-3 text-xs text-amber-700">
        本次请求未返回检索过程。请开启“显示检索过程”后重新运行检索。
      </div>
    );
  }
  return (
    <div className="mt-5 space-y-3">
      <h3 className="text-sm font-medium">检索过程</h3>
      <TraceSection title="Vector 召回" items={trace.recall.vector || []} render={renderRecallHit} />
      <TraceSection title="BM25 召回" items={trace.recall.bm25 || []} render={renderRecallHit} />
      <TraceSection
        title="Fusion"
        items={trace.fusion}
        render={(item) =>
          `${item.chunk_id} → ${item.parent_id} / score=${item.fusion_score.toFixed(4)} / ${item.sources.join(', ')}`
        }
      />
      <TraceSection
        title="Aggregation"
        items={trace.aggregation}
        render={(item) =>
          `${item.parent_id} / score=${item.fusion_score.toFixed(4)} / children=${item.hit_child_count}`
        }
      />
      <TraceSection
        title="Rerank / Final"
        items={trace.rerank}
        render={(item) =>
          `${item.parent_id} / ${item.before_rank} → ${item.after_rank} / fusion=${item.fusion_score.toFixed(4)} / final=${item.final_score.toFixed(4)}`
        }
      />
    </div>
  );
}

function TraceSection<T>({
  title,
  items,
  render,
}: {
  title: string;
  items: T[];
  render: (item: T) => string;
}) {
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-xs font-medium text-gray-700">{title}</h4>
        <span className="text-xs text-gray-400">{items.length} 条</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-gray-400">无结果</p>
      ) : (
        <div className="max-h-48 space-y-1 overflow-auto">
          {items.map((item, idx) => (
            <p key={idx} className="truncate rounded bg-gray-50 px-2 py-1 text-xs text-gray-600">
              {render(item)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function renderRecallHit(item: {
  rank: number;
  chunk_id: string;
  parent_id: string;
  score: number;
}) {
  return `#${item.rank} ${item.chunk_id} → ${item.parent_id} / score=${item.score.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// 设置 Tab
// ---------------------------------------------------------------------------
function SettingsTab({
  kb,
  onDeleteKb,
  deleting,
}: {
  kb: KnowledgeBase;
  onDeleteKb: () => void;
  deleting: boolean;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState({
    name: kb.name,
    description: kb.description || '',
    visibility: kb.visibility,
    chunk_size: kb.chunk_size,
    chunk_overlap: kb.chunk_overlap,
  });
  const invalidChunkConfig = draft.chunk_overlap >= draft.chunk_size;

  const updateMutation = useMutation({
    mutationFn: () =>
      kbApi.update(kb.id, {
        name: draft.name.trim() || undefined,
        description: draft.description.trim(),
        visibility: draft.visibility,
        chunk_size: draft.chunk_size,
        chunk_overlap: draft.chunk_overlap,
      }),
    onSuccess: () => {
      toast.success('已保存');
      queryClient.invalidateQueries({ queryKey: ['kb', kb.id] });
      queryClient.invalidateQueries({ queryKey: ['kb-list'] });
    },
    onError: (e) => toast.error((e as ApiError).message || '保存失败'),
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="space-y-4">
        <h2 className="text-sm font-medium">基础设置</h2>
        <Field label="名称">
          <input
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
          />
        </Field>
        <Field label="描述">
          <textarea
            value={draft.description}
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            rows={4}
            className="w-full resize-none rounded-md border px-3 py-2 text-sm outline-none focus:border-gray-400"
          />
        </Field>
        <Field label="可见性">
          <div className="flex gap-2">
            {(['private', 'workspace', 'public'] as Visibility[]).map((visibility) => (
              <button
                key={visibility}
                onClick={() => setDraft({ ...draft, visibility })}
                className={cn(
                  'flex-1 rounded-md border px-3 py-1.5 text-xs transition-colors',
                  draft.visibility === visibility
                    ? 'border-gray-900 bg-gray-900 text-white'
                    : 'border-gray-200 hover:bg-gray-50',
                )}
              >
                {visibility === 'private' ? '私有' : visibility === 'workspace' ? '团队' : '公开'}
              </button>
            ))}
          </div>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="分块大小">
            <input
              type="number"
              min={64}
              max={4096}
              value={draft.chunk_size}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  chunk_size: Math.max(64, Math.min(4096, Number(event.target.value) || 64)),
                })
              }
              className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <Field label="重叠长度">
            <input
              type="number"
              min={0}
              max={512}
              value={draft.chunk_overlap}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  chunk_overlap: Math.max(0, Math.min(512, Number(event.target.value) || 0)),
                })
              }
              className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
        </div>
        <p className="text-xs leading-5 text-gray-400">
          分块参数影响后续上传和重新入库；已有文档不会自动重切。
        </p>
        {invalidChunkConfig && (
          <p className="text-xs leading-5 text-red-600">重叠长度必须小于分块大小。</p>
        )}
        <button
          onClick={() => updateMutation.mutate()}
          disabled={invalidChunkConfig || updateMutation.isPending}
          className="flex items-center gap-2 rounded-md bg-black px-3 py-1.5 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
        >
          <Save size={15} />
          {updateMutation.isPending ? '保存中…' : '保存设置'}
        </button>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium">危险操作</h2>
        <div className="rounded-lg border border-red-100 bg-red-50 p-4">
          <p className="text-sm leading-6 text-red-700">
            删除后会移除该知识库及其全部文档、向量与倒排索引，操作不可恢复。
          </p>
          <button
            onClick={onDeleteKb}
            disabled={deleting}
            className="mt-4 flex items-center gap-2 rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
          >
            <Trash2 size={15} />
            删除知识库
          </button>
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------
function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-20 rounded-lg border border-gray-200 px-3 py-2">
      <div className="text-sm font-semibold">{value}</div>
      <div className="mt-0.5 text-[11px] text-gray-400">{label}</div>
    </div>
  );
}

function TabButton({
  active,
  icon,
  onClick,
  children,
}: {
  active: boolean;
  icon: ReactNode;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 border-b-2 px-4 py-2 text-sm transition-colors',
        active ? 'border-black text-black' : 'border-transparent text-gray-500 hover:text-gray-900',
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function DocumentRow({
  doc,
  onViewChunks,
  onDelete,
  onRebuild,
  onReingest,
  rebuilding,
  reingesting,
}: {
  doc: KnowledgeDocument;
  onViewChunks: () => void;
  onDelete: () => void;
  onRebuild: () => void;
  onReingest: () => void;
  rebuilding: boolean;
  reingesting: boolean;
}) {
  const inProgress = DOC_ACTIVE_STATUSES.has(doc.status);
  const busy = inProgress || doc.vector_index_status === 'rebuilding';
  return (
    <div className="flex items-center gap-4 border-b border-gray-100 px-4 py-3 last:border-b-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100">
        <FileText size={17} className="text-gray-500" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium">{doc.name}</p>
          <DocStatusBadge status={doc.status} />
          {doc.status === 'indexed' && <VectorBadge status={doc.vector_index_status} />}
        </div>
        <p className="mt-0.5 text-xs text-gray-400">
          {formatBytes(doc.size_bytes)} / {doc.chunk_count} 分块 / {doc.mime_type}
        </p>
        {inProgress && (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-gray-100">
            <div className="h-1 rounded bg-blue-500 transition-all" style={{ width: `${doc.progress}%` }} />
          </div>
        )}
        {doc.status === 'failed' && doc.status_message && (
          <p className="mt-1 text-xs text-red-600">入库失败：{doc.status_message}</p>
        )}
        {doc.status === 'indexed' && doc.vector_index_status === 'failed' && doc.vector_index_error && (
          <p className="mt-1 text-xs text-red-600">向量索引失败：{doc.vector_index_error}</p>
        )}
      </div>
      <button
        onClick={onViewChunks}
        disabled={doc.status !== 'indexed'}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-40"
      >
        <Layers size={12} />
        查看分块
      </button>
      <button
        onClick={onRebuild}
        disabled={doc.status !== 'indexed' || busy || rebuilding}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-40"
      >
        <RefreshCw size={12} />
        重建向量
      </button>
      <button
        onClick={onReingest}
        disabled={busy || reingesting}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-40"
      >
        <RefreshCw size={12} />
        重新入库
      </button>
      <button
        onClick={onDelete}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-red-50 hover:text-red-600"
      >
        <Trash2 size={12} />
        删除
      </button>
    </div>
  );
}

function DocStatusBadge({ status }: { status: KnowledgeDocument['status'] }) {
  const map: Record<KnowledgeDocument['status'], [string, string]> = {
    pending: ['bg-gray-100 text-gray-600', '排队中'],
    parsing: ['bg-blue-50 text-blue-700', '解析中'],
    chunking: ['bg-blue-50 text-blue-700', '分块中'],
    embedding: ['bg-blue-50 text-blue-700', '向量化'],
    indexed: ['bg-green-50 text-green-700', '已入库'],
    failed: ['bg-red-50 text-red-700', '失败'],
  };
  const [cls, label] = map[status];
  return <span className={cn('rounded px-1.5 py-0.5 text-[10px]', cls)}>{label}</span>;
}

function VectorBadge({ status }: { status: KnowledgeDocument['vector_index_status'] }) {
  const map: Record<KnowledgeDocument['vector_index_status'], [string, string]> = {
    ready: ['bg-green-50 text-green-700', '向量就绪'],
    stale: ['bg-amber-50 text-amber-700', '向量待重建'],
    rebuilding: ['bg-blue-50 text-blue-700', '向量重建中'],
    failed: ['bg-red-50 text-red-700', '向量失败'],
  };
  const [cls, label] = map[status];
  return <span className={cn('rounded px-1.5 py-0.5 text-[10px]', cls)}>{label}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-gray-600">{label}</span>
      {children}
    </label>
  );
}
