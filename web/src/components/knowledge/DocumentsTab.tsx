import { useRef, useState, DragEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Upload, Trash2, RefreshCw, FileText, AlertCircle, CheckCircle2 } from 'lucide-react';
import { documentsApi } from '@/api';
import { DocumentStatus, KnowledgeDocument } from '@/types';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';

interface Props {
  kbId: string;
}

export function DocumentsTab({ kbId }: Props) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  // 轮询(若有解析中的文档,5秒拉一次)
  const { data, isLoading } = useQuery({
    queryKey: ['documents', kbId],
    queryFn: () => documentsApi.list(kbId, { page: 1, page_size: 100 }),
    refetchInterval: (q) => {
      const items = q.state.data?.items;
      if (!items) return false;
      const hasInProgress = items.some(
        (d) => (d.status !== 'indexed' && d.status !== 'failed')
          || d.vector_index_status === 'rebuilding',
      );
      return hasInProgress ? 1500 : false;
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.upload(kbId, file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['documents', kbId] });
      qc.invalidateQueries({ queryKey: ['knowledge-base', kbId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (docId: string) => documentsApi.remove(kbId, docId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['documents', kbId] });
      qc.invalidateQueries({ queryKey: ['knowledge-base', kbId] });
    },
  });

  function handleFiles(files: FileList | File[]) {
    Array.from(files).forEach((f) => uploadMutation.mutate(f));
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) handleFiles(e.dataTransfer.files);
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-4xl mx-auto">
        {/* 拖拽上传区 */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className={cn(
            'border-2 border-dashed rounded-lg py-8 text-center cursor-pointer transition-colors',
            dragOver
              ? 'border-blue-400 bg-blue-50'
              : 'border-gray-200 hover:border-gray-300',
          )}
        >
          <Upload className="mx-auto text-gray-400 mb-2" size={24} />
          <p className="text-sm text-gray-700">点击或拖拽文件到此处上传</p>
          <p className="text-xs text-gray-400 mt-1">支持 PDF / Word / Markdown / TXT</p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) handleFiles(e.target.files);
              e.target.value = '';
            }}
          />
        </div>

        {/* 文档列表 */}
        <div className="mt-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium">
              文档 {data ? `(${data.total})` : ''}
            </h3>
          </div>
          {isLoading ? (
            <div className="text-sm text-gray-400 py-8 text-center">加载中…</div>
          ) : !data || data.items.length === 0 ? (
            <div className="text-sm text-gray-400 py-8 text-center">还没有文档</div>
          ) : (
            <div className="border rounded-lg divide-y">
              {data.items.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  doc={doc}
                  onDelete={async () => {
                    if (await confirm({ message: `删除文档「${doc.name}」?`, confirmLabel: '删除', danger: true })) {
                      deleteMutation.mutate(doc.id);
                    }
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DocumentRow({
  doc, onDelete,
}: {
  doc: KnowledgeDocument;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 hover:bg-gray-50">
      <FileText className="text-gray-400 shrink-0" size={16} />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{doc.name}</div>
        <div className="flex items-center gap-2 text-xs text-gray-400 mt-0.5">
          <span>{formatBytes(doc.size_bytes)}</span>
          {doc.status === 'indexed' && (
            <>
              <span>·</span>
              <span>{doc.chunk_count} 分块</span>
            </>
          )}
        </div>
      </div>
      <StatusIndicator doc={doc} />
      <button
        onClick={onDelete}
        className="text-gray-400 hover:text-red-500 transition-colors"
        title="删除"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function StatusIndicator({ doc }: { doc: KnowledgeDocument }) {
  const status = doc.status;

  if (status === 'indexed') {
    if (doc.vector_index_status === 'stale') {
      return (
        <span className="flex items-center gap-1 text-xs text-amber-600" title="BM25 检索可用，向量索引需要重建">
          <AlertCircle size={13} />
          BM25 可用 · 向量待重建
        </span>
      );
    }
    if (doc.vector_index_status === 'rebuilding') {
      return (
        <span className="flex items-center gap-1 text-xs text-blue-600">
          <RefreshCw size={12} className="animate-spin" />
          向量重建中
        </span>
      );
    }
    if (doc.vector_index_status === 'failed') {
      return (
        <span className="flex items-center gap-1 text-xs text-red-600" title={doc.vector_index_error}>
          <AlertCircle size={13} />
          向量索引失败
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-xs text-green-600">
        <CheckCircle2 size={13} />
        已索引
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="flex items-center gap-1 text-xs text-red-600" title={doc.status_message}>
        <AlertCircle size={13} />
        失败
      </span>
    );
  }

  // 处理中:展示进度
  return (
    <div className="flex items-center gap-2">
      <RefreshCw size={12} className="text-blue-500 animate-spin" />
      <div className="text-xs text-blue-600">{statusLabel(status)}</div>
      <div className="w-16 h-1 bg-gray-200 rounded overflow-hidden">
        <div
          className="h-full bg-blue-500 transition-all"
          style={{ width: `${doc.progress}%` }}
        />
      </div>
    </div>
  );
}

function statusLabel(s: DocumentStatus): string {
  return {
    pending: '排队中',
    parsing: '解析中',
    chunking: '分块中',
    embedding: '向量化',
    indexed: '完成',
    failed: '失败',
  }[s];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
