import { useEffect, useState } from 'react';
import { X, Download, Loader2 } from 'lucide-react';
import type { ChatFileMeta, ChatFilePreview } from '@/types';
import { filesApi } from '@/api';
import { MarkdownContent } from './MarkdownContent';

interface Props {
  file: ChatFileMeta;
  onClose: () => void;
}

/** 扩展名 → markdown 代码块语言, 复用 MarkdownContent 高亮 */
function langFromName(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    py: 'python', js: 'javascript', mjs: 'javascript', ts: 'typescript',
    tsx: 'tsx', jsx: 'jsx', java: 'java', go: 'go', rs: 'rust',
    c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', cs: 'csharp', kt: 'kotlin',
    sh: 'bash', bash: 'bash', zsh: 'bash', yml: 'yaml', yaml: 'yaml',
    json: 'json', toml: 'toml', md: 'markdown', html: 'html', xml: 'xml',
    css: 'css', scss: 'scss', sql: 'sql', rb: 'ruby', php: 'php', swift: 'swift',
  };
  return map[ext] || '';
}

/** 右侧文件预览面板: 复用 CitationPanel 的 aside 布局 + MarkdownContent 代码高亮 + 下载 */
export function FilePreviewPanel({ file, onClose }: Props) {
  const [preview, setPreview] = useState<ChatFilePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setPreview(null);
    filesApi
      .previewContent(file.id)
      .then((p) => alive && setPreview(p))
      .catch(() => alive && setError('加载失败'))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [file.id]);

  const lang = langFromName(file.name);
  const md = preview ? `\`\`\`${lang}\n${preview.text}\n\`\`\`` : '';

  return (
    <aside className="flex h-full w-[380px] shrink-0 flex-col border-l bg-white">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="min-w-0 truncate text-sm font-medium" title={file.name}>
          {file.name}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={() => filesApi.download(file.id, file.name)}
            title="下载"
            className="p-1 text-gray-400 hover:text-gray-700"
          >
            <Download size={15} />
          </button>
          <button onClick={onClose} title="关闭" className="p-1 text-gray-400 hover:text-gray-700">
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-3 text-[13px]">
        {loading ? (
          <div className="mt-8 flex items-center justify-center gap-2 text-xs text-gray-400">
            <Loader2 size={14} className="animate-spin" /> 加载中…
          </div>
        ) : error ? (
          <div className="mt-8 text-center text-xs text-red-500">{error}</div>
        ) : preview ? (
          <>
            <MarkdownContent content={md} />
            {preview.truncated && (
              <div className="mt-2 text-center text-[11px] text-gray-400">
                已截断显示 (共 {preview.total_lines} 行)
              </div>
            )}
          </>
        ) : null}
      </div>
    </aside>
  );
}
