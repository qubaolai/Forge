import { useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Database,
  FilePlus2,
  FileText,
  RefreshCw,
  Save,
  Search,
  Settings,
  SlidersHorizontal,
  Trash2,
  Upload,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type { KnowledgeDocument, RetrievalResult, Visibility } from '@/types';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import {
  loadKnowledgeStore,
  makeUploadedDocument,
  mockRetrieve,
  recalcKnowledgeBase,
  saveKnowledgeStore,
} from './mockKnowledgeStore';
import { formatBytes, VisibilityBadge } from './KnowledgeListPage';

type TabKey = 'documents' | 'retrieval' | 'settings';

export default function KnowledgeDetailPage() {
  const { kbId } = useParams();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [store, setStore] = useState(loadKnowledgeStore);
  const [tab, setTab] = useState<TabKey>('documents');
  const [query, setQuery] = useState('如何使用这个知识库回答问题?');
  const [topK, setTopK] = useState(5);
  const [threshold, setThreshold] = useState(0.45);
  const [hybrid, setHybrid] = useState(true);
  const [rerank, setRerank] = useState(false);
  const [results, setResults] = useState<RetrievalResult[]>([]);

  const kb = store.kbs.find((item) => item.id === kbId);
  const docs = useMemo(
    () => store.docs.filter((doc) => doc.kb_id === kbId),
    [store.docs, kbId],
  );

  const [draft, setDraft] = useState(() => ({
    name: kb?.name || '',
    description: kb?.description || '',
    visibility: kb?.visibility || 'private',
    chunkSize: kb?.chunk_size || 500,
    chunkOverlap: kb?.chunk_overlap || 50,
  }));

  function persist(next: typeof store) {
    setStore(next);
    saveKnowledgeStore(next);
  }

  if (!kb) {
    return (
      <div className="flex h-full items-center justify-center bg-white">
        <div className="text-center">
          <Database className="mx-auto mb-3 text-gray-300" size={42} />
          <h1 className="text-base font-semibold">知识库不存在</h1>
          <p className="mt-1 text-sm text-gray-500">可能已被删除，或静态数据已重置。</p>
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

  const currentKb = kb;

  function updateDocs(nextDocs: KnowledgeDocument[]) {
    const nextKb = recalcKnowledgeBase(currentKb, nextDocs);
    persist({
      kbs: store.kbs.map((item) => (item.id === currentKb.id ? nextKb : item)),
      docs: nextDocs,
    });
  }

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const uploaded = Array.from(files).map((file) => makeUploadedDocument(currentKb.id, file));
    updateDocs([...uploaded, ...store.docs]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function removeDoc(doc: KnowledgeDocument) {
    const ok = await confirm({
      title: '删除文档',
      message: `确认删除「${doc.name}」?`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!ok) return;
    updateDocs(store.docs.filter((item) => item.id !== doc.id));
  }

  function rebuildDoc(doc: KnowledgeDocument) {
    const now = new Date().toISOString();
    updateDocs(
      store.docs.map((item) =>
        item.id === doc.id
          ? { ...item, vector_index_status: 'ready', updated_at: now, vector_indexed_at: now }
          : item,
      ),
    );
  }

  function runRetrieve() {
    const matched = mockRetrieve(query, docs)
      .filter((item) => item.score >= threshold)
      .slice(0, topK);
    setResults(matched);
  }

  function saveSettings() {
    const now = new Date().toISOString();
    persist({
      ...store,
      kbs: store.kbs.map((item) =>
        item.id === currentKb.id
          ? {
              ...item,
              name: draft.name.trim() || item.name,
              description: draft.description.trim() || undefined,
              visibility: draft.visibility as Visibility,
              chunk_size: Math.max(100, draft.chunkSize),
              chunk_overlap: Math.max(0, Math.min(draft.chunkOverlap, draft.chunkSize - 1)),
              updated_at: now,
            }
          : item,
      ),
    });
  }

  async function removeKb() {
    const ok = await confirm({
      title: '删除知识库',
      message: `确认删除「${currentKb.name}」? 关联文档也会从静态页面中移除。`,
      confirmLabel: '删除',
      danger: true,
    });
    if (!ok) return;
    persist({
      kbs: store.kbs.filter((item) => item.id !== currentKb.id),
      docs: store.docs.filter((doc) => doc.kb_id !== currentKb.id),
    });
    navigate('/knowledge');
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
              <h1 className="text-xl font-semibold">{currentKb.name}</h1>
              <VisibilityBadge visibility={currentKb.visibility} />
            </div>
            <p className="max-w-2xl text-sm leading-6 text-gray-500">
              {currentKb.description || '暂无描述。可在设置中补充知识库用途、范围和维护说明。'}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <Stat label="文档" value={currentKb.document_count} />
            <Stat label="分块" value={currentKb.chunk_count} />
            <Stat label="容量" value={formatBytes(currentKb.size_bytes)} />
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
              <h2 className="mb-3 text-sm font-medium">操作文档</h2>
              <div className="space-y-3 rounded-lg border border-gray-200 p-4 text-sm leading-6 text-gray-600">
                <p>上传文件后会在本地静态数据中生成文档记录，并模拟完成解析、分块和向量索引。</p>
                <p>后续接入后端时，这里对应上传、解析任务状态、索引重建和删除接口。</p>
                <p>当前页面数据保存在浏览器 localStorage，刷新后仍可查看，清理浏览器数据会重置。</p>
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
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-md bg-black px-3 py-2 text-sm text-white hover:bg-gray-800"
              >
                <Upload size={15} />
                上传文档
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
                    <DocumentRow key={doc.id} doc={doc} onDelete={() => removeDoc(doc)} onRebuild={() => rebuildDoc(doc)} />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {tab === 'retrieval' && (
          <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
            <section className="space-y-4">
              <h2 className="text-sm font-medium">检索配置</h2>
              <Field label="查询内容">
                <textarea
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  rows={4}
                  className="w-full resize-none rounded-md border px-3 py-2 text-sm outline-none focus:border-gray-400"
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Top K">
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={topK}
                    onChange={(event) => setTopK(Number(event.target.value))}
                    className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
                  />
                </Field>
                <Field label="分数阈值">
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={threshold}
                    onChange={(event) => setThreshold(Number(event.target.value))}
                    className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
                  />
                </Field>
              </div>
              <Toggle checked={hybrid} onChange={setHybrid} label="混合检索" />
              <Toggle checked={rerank} onChange={setRerank} label="重排序" />
              <button
                onClick={runRetrieve}
                className="flex w-full items-center justify-center gap-2 rounded-md bg-black px-3 py-2 text-sm text-white hover:bg-gray-800"
              >
                <Search size={15} />
                运行静态检索
              </button>
            </section>

            <section>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium">召回结果</h2>
                <span className="flex items-center gap-1 text-xs text-gray-400">
                  <SlidersHorizontal size={12} />
                  {hybrid ? 'Hybrid' : 'Vector'} / {rerank ? 'Rerank' : 'No rerank'}
                </span>
              </div>
              {results.length === 0 ? (
                <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
                  <Search className="mx-auto text-gray-300" size={38} />
                  <p className="mt-3 text-sm text-gray-500">运行检索后显示静态召回结果</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {results.map((result) => (
                    <div key={result.chunk.id} className="rounded-lg border border-gray-200 p-4">
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-medium">{result.document.name}</span>
                        <span className="rounded bg-green-50 px-2 py-0.5 text-xs text-green-700">
                          {result.score.toFixed(2)}
                        </span>
                      </div>
                      <p className="text-sm leading-6 text-gray-600">{result.chunk.content}</p>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {tab === 'settings' && (
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
                    min={100}
                    value={draft.chunkSize}
                    onChange={(event) => setDraft({ ...draft, chunkSize: Number(event.target.value) })}
                    className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
                  />
                </Field>
                <Field label="重叠长度">
                  <input
                    type="number"
                    min={0}
                    value={draft.chunkOverlap}
                    onChange={(event) => setDraft({ ...draft, chunkOverlap: Number(event.target.value) })}
                    className="w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-gray-400"
                  />
                </Field>
              </div>
              <button
                onClick={saveSettings}
                className="flex items-center gap-2 rounded-md bg-black px-3 py-1.5 text-sm text-white hover:bg-gray-800"
              >
                <Save size={15} />
                保存设置
              </button>
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium">危险操作</h2>
              <div className="rounded-lg border border-red-100 bg-red-50 p-4">
                <p className="text-sm leading-6 text-red-700">
                  删除后会移除当前静态知识库和关联文档。接入后端后应要求二次确认并记录审计日志。
                </p>
                <button
                  onClick={removeKb}
                  className="mt-4 flex items-center gap-2 rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700"
                >
                  <Trash2 size={15} />
                  删除知识库
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

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
  onDelete,
  onRebuild,
}: {
  doc: KnowledgeDocument;
  onDelete: () => void;
  onRebuild: () => void;
}) {
  return (
    <div className="flex items-center gap-4 border-b border-gray-100 px-4 py-3 last:border-b-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100">
        <FileText size={17} className="text-gray-500" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{doc.name}</p>
          <StatusBadge status={doc.vector_index_status} />
        </div>
        <p className="mt-0.5 text-xs text-gray-400">
          {formatBytes(doc.size_bytes)} / {doc.chunk_count} 分块 / {doc.mime_type}
        </p>
      </div>
      <button
        onClick={onRebuild}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-50"
      >
        <RefreshCw size={12} />
        重建索引
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

function StatusBadge({ status }: { status: KnowledgeDocument['vector_index_status'] }) {
  const config = {
    ready: 'bg-green-50 text-green-700',
    stale: 'bg-amber-50 text-amber-700',
    rebuilding: 'bg-blue-50 text-blue-700',
    failed: 'bg-red-50 text-red-700',
  }[status];
  const label = {
    ready: '可用',
    stale: '需重建',
    rebuilding: '重建中',
    failed: '失败',
  }[status];
  return <span className={cn('rounded px-1.5 py-0.5 text-[10px]', config)}>{label}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-gray-600">{label}</span>
      {children}
    </label>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between rounded-md border px-3 py-2 text-sm">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4"
      />
    </label>
  );
}
