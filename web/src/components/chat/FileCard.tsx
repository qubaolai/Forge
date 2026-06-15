import { FileCode, Download, Eye } from 'lucide-react';
import type { ChatFileMeta } from '@/types';

/** 字节数 → 人类可读 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

interface Props {
  file: ChatFileMeta;
  onPreview?: (file: ChatFileMeta) => void;
  onDownload?: (file: ChatFileMeta) => void;
}

/** 会话文件卡片 (上传附件 / write_file 生成); 点击文件名或预览图标打开预览面板 */
export function FileCard({ file, onPreview, onDownload }: Props) {
  return (
    <div className="flex max-w-[360px] items-center gap-2.5 rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-500">
        <FileCode size={16} />
      </div>
      <button
        onClick={() => onPreview?.(file)}
        className="min-w-0 flex-1 text-left"
        title="预览"
      >
        <div className="truncate text-[13px] font-medium text-gray-800">{file.name}</div>
        <div className="text-[11px] text-gray-400">{formatSize(file.size_bytes)}</div>
      </button>
      <div className="flex shrink-0 items-center gap-0.5">
        {onPreview && (
          <button
            onClick={() => onPreview(file)}
            title="预览"
            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            <Eye size={14} />
          </button>
        )}
        {onDownload && (
          <button
            onClick={() => onDownload(file)}
            title="下载"
            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            <Download size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
