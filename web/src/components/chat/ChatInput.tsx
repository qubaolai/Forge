import { useEffect, useRef, useState, KeyboardEvent, ClipboardEvent, ChangeEvent } from 'react';
import { Send, Square, Brain, Paperclip, X, Loader2, BookOpen, Check } from 'lucide-react';
import type { KnowledgeBase, ModelGroup } from '@/types';
import { cn } from '@/lib/utils';

export type ThinkingLevel = 'low' | 'medium' | 'high' | 'xhigh';

interface Props {
  onSend: (text: string, attachments?: { file_id: string; type: string }[]) => void;
  // 上传会话附件 (超阈值大段输入 / 文件选择), 返回文件元数据; 不传则不显示附件入口
  onUploadAttachment?: (file: File) => Promise<{ id: string; name: string }>;
  onAbort?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  thinkingLevel?: ThinkingLevel;
  onThinkingLevelChange?: (value: ThinkingLevel) => void;
  // 外部预填文本（如点击示例提示词），写入输入框并聚焦
  prefill?: string;
  // 模型选择
  selectedProvider: string;
  selectedModel: string;
  modelGroups: ModelGroup[];
  onModelChange: (provider: string, model: string) => void;
  thinkingEnabled: boolean;
  onThinkingChange: (enabled: boolean) => void;
  knowledgeBases?: KnowledgeBase[];
  selectedKbIds?: string[];
  onSelectedKbIdsChange?: (ids: string[]) => void;
}

const THINKING_LEVEL_LABELS: Record<ThinkingLevel, string> = {
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '超高',
};

// 允许上传的扩展名 (与后端 file_validation 白名单对齐): 文档 / office / pdf / 源码。
// 不含可执行程序与脚本 (.exe/.sh/.bat/.ps1 等), 防木马由后端 magic-byte 二次把关。
const ALLOWED_UPLOAD_EXTS = [
  '.txt', '.md', '.markdown', '.rtf', '.csv', '.log',
  '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf',
  '.py', '.pyi', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.vue',
  '.java', '.kt', '.scala', '.go', '.rs', '.c', '.h', '.cpp', '.hpp',
  '.cc', '.cxx', '.cs', '.rb', '.php', '.swift', '.m', '.mm', '.lua',
  '.pl', '.r', '.dart', '.sql', '.json', '.yaml', '.yml', '.toml',
  '.ini', '.cfg', '.xml', '.html', '.htm', '.css', '.scss', '.less',
  '.tex', '.gradle', '.proto', '.graphql', '.tsv', '.env',
];
// input accept 属性: 扩展名列表 (浏览器只做提示, 真正限制在 JS 校验 + 后端)。
const UPLOAD_ACCEPT = ALLOWED_UPLOAD_EXTS.join(',');

function fileExt(name: string): string {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function isAllowedUpload(file: File): boolean {
  return ALLOWED_UPLOAD_EXTS.includes(fileExt(file.name));
}

export function ChatInput({
  onSend,
  onAbort,
  disabled,
  streaming,
  placeholder,
  thinkingLevel = 'medium',
  onThinkingLevelChange,
  prefill,
  selectedProvider,
  selectedModel,
  modelGroups,
  onModelChange,
  thinkingEnabled,
  onThinkingChange,
  onUploadAttachment,
  knowledgeBases = [],
  selectedKbIds = [],
  onSelectedKbIdsChange,
}: Props) {
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<{ id: string; name: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const kbPickerRef = useRef<HTMLDivElement>(null);

  // 知识库选择框: 点击选择框以外的任意区域即关闭
  useEffect(() => {
    if (!kbPickerOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (kbPickerRef.current && !kbPickerRef.current.contains(event.target as Node)) {
        setKbPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [kbPickerOpen]);
  // 粘贴文本超过该字符数时自动转为会话附件 (降上下文占用, 后端 read_file 按需读取)
  const PASTE_THRESHOLD = 4000;

  async function uploadFile(file: File) {
    if (!onUploadAttachment) return;
    // 前置类型校验: 不允许可执行程序/脚本等非白名单类型 (后端还会二次把关)
    if (!isAllowedUpload(file)) {
      setUploadError(`不支持的文件类型: ${file.name}; 仅支持文档/表格/PDF 及源代码文件`);
      return;
    }
    setUploadError('');
    setUploading(true);
    try {
      const res = await onUploadAttachment(file);
      setAttachments((prev) => [...prev, { id: res.id, name: res.name }]);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : `上传失败: ${file.name}`);
    } finally {
      setUploading(false);
    }
  }

  function handleFilePick(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (files) Array.from(files).forEach((f) => uploadFile(f));
    e.target.value = '';
  }

  function handlePaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (!onUploadAttachment) return;
    const text = e.clipboardData.getData('text');
    if (text && text.length > PASTE_THRESHOLD) {
      e.preventDefault();
      uploadFile(new File([text], `pasted-${Date.now()}.txt`, { type: 'text/plain' }));
    }
  }

  function removeAttachment(id: string) {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }

  function autoResize() {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
  }

  // 外部预填（点击示例提示词）：写入输入框并聚焦
  useEffect(() => {
    if (!prefill) return;
    setValue(prefill);
    const ta = textareaRef.current;
    if (ta) {
      ta.focus();
      requestAnimationFrame(autoResize);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  function handleSubmit() {
    const text = value.trim();
    // uploading 时不发送: 附件未就绪会导致会话 id 竞态 / 附件丢失
    if ((!text && attachments.length === 0) || disabled || uploading) return;
    onSend(
      text,
      attachments.length > 0 ? attachments.map((a) => ({ file_id: a.id, type: 'file' })) : undefined,
    );
    setValue('');
    setAttachments([]);
    requestAnimationFrame(autoResize);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  }

  // 从选中模型元数据中获取思考能力
  const currentModel = modelGroups
    .find((group) => group.provider === selectedProvider)
    ?.models.find((m) => m.name === selectedModel);
  const thinkingMeta = currentModel?.thinking;
  const hasThinking = Boolean(currentModel?.config?.capabilities?.includes('thinking'));
  const thinkingOptions = (thinkingMeta?.options || []).filter(Boolean) as string[];
  const showThinkingLevel = hasThinking && thinkingOptions.length > 0 && onThinkingLevelChange;
  const selectedThinkingLevel = (
    thinkingOptions.includes(thinkingLevel) ? thinkingLevel : thinkingOptions[0]
  ) as ThinkingLevel;
  const selectedValue = `${selectedProvider}::${selectedModel}`;
  const selectedKbSet = new Set(selectedKbIds);
  const selectedKbCount = selectedKbIds.length;

  function toggleKb(id: string) {
    if (!onSelectedKbIdsChange) return;
    const next = selectedKbSet.has(id)
      ? selectedKbIds.filter((item) => item !== id)
      : [...selectedKbIds, id];
    onSelectedKbIdsChange(next);
  }

  return (
    <div className="border-t bg-white px-6 py-4">
      <div className="mx-auto max-w-[760px]">
        {onUploadAttachment && (attachments.length > 0 || uploading) && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((a) => (
              <div
                key={a.id}
                className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[12px] text-gray-600"
              >
                <Paperclip size={12} className="text-gray-400" />
                <span className="max-w-[160px] truncate" title={a.name}>{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.id)}
                  className="text-gray-400 hover:text-gray-700"
                  title="移除"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            {uploading && (
              <div className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[12px] text-gray-400">
                <Loader2 size={12} className="animate-spin" /> 上传中…
              </div>
            )}
          </div>
        )}
        {uploadError && (
          <div className="mb-2 flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2 py-1 text-[12px] text-red-600">
            <X size={12} className="shrink-0 text-red-400" />
            <span className="flex-1">{uploadError}</span>
            <button onClick={() => setUploadError('')} className="text-red-400 hover:text-red-700" title="关闭">
              <X size={12} />
            </button>
          </div>
        )}
        <div
          className="flex items-end gap-2 rounded-2xl border px-3 py-2.5 transition-colors
            focus-within:border-gray-400 focus-within:shadow-sm"
        >
          {onUploadAttachment && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={UPLOAD_ACCEPT}
                className="hidden"
                onChange={handleFilePick}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={disabled}
                title="添加附件"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50"
              >
                <Paperclip size={16} />
              </button>
            </>
          )}
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              autoResize();
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={placeholder || '输入消息,Shift+Enter 换行'}
            rows={1}
            className="max-h-[200px] flex-1 resize-none bg-transparent py-1 text-[15px] leading-[1.7] outline-none placeholder:text-gray-400"
            disabled={disabled}
          />
          {streaming ? (
            <button
              onClick={onAbort}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 transition-colors hover:bg-gray-200"
              title="停止生成"
            >
              <Square size={15} className="fill-gray-700" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={(!value.trim() && attachments.length === 0) || disabled || uploading}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-black text-white transition-colors disabled:bg-gray-300 disabled:text-gray-500"
              title="发送 (Enter)"
            >
              <Send size={15} />
            </button>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[12px] text-gray-400">
          <div className="flex items-center gap-2">
            {onSelectedKbIdsChange && (
              <div className="relative" ref={kbPickerRef}>
                <button
                  type="button"
                  onClick={() => setKbPickerOpen((open) => !open)}
                  className={cn(
                    'flex items-center gap-1.5 rounded border px-2 py-0.5 transition-colors',
                    selectedKbCount > 0
                      ? 'border-blue-200 bg-blue-50 text-blue-700'
                      : 'border-gray-200 text-gray-500 hover:text-gray-700',
                  )}
                  title={selectedKbCount > 0 ? '已选择知识库' : '未选择知识库, 本轮不会查询'}
                >
                  <BookOpen size={13} />
                  <span>{selectedKbCount > 0 ? `知识库 ${selectedKbCount}` : '知识库'}</span>
                </button>
                {kbPickerOpen && (
                  <div className="absolute bottom-full left-0 z-20 mb-2 w-72 rounded-lg border border-gray-200 bg-white p-2 shadow-lg">
                    <div className="mb-1 flex items-center justify-between px-1 text-[11px] text-gray-400">
                      <span>选择本轮要查询的知识库</span>
                      {selectedKbCount > 0 && (
                        <button
                          type="button"
                          onClick={() => onSelectedKbIdsChange([])}
                          className="text-gray-400 hover:text-gray-700"
                        >
                          清空
                        </button>
                      )}
                    </div>
                    {knowledgeBases.length === 0 ? (
                      <div className="px-2 py-3 text-center text-xs text-gray-400">
                        暂无可用知识库
                      </div>
                    ) : (
                      <div className="max-h-56 overflow-y-auto">
                        {knowledgeBases.map((kb) => {
                          const selected = selectedKbSet.has(kb.id);
                          return (
                            <button
                              key={kb.id}
                              type="button"
                              onClick={() => toggleKb(kb.id)}
                              className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left hover:bg-gray-50"
                            >
                              <span
                                className={cn(
                                  'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                                  selected
                                    ? 'border-blue-500 bg-blue-500 text-white'
                                    : 'border-gray-300 text-transparent',
                                )}
                              >
                                <Check size={11} />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-xs font-medium text-gray-700">
                                  {kb.name}
                                </span>
                                <span className="block truncate text-[11px] text-gray-400">
                                  {kb.document_count} 文档 · {kb.chunk_count} 分块
                                </span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            <select
              value={selectedValue}
              onChange={(e) => {
                const [provider, model] = e.target.value.split('::');
                if (provider && model) {
                  onModelChange(provider, model);
                }
              }}
              className="bg-transparent border rounded px-1.5 py-0.5 text-gray-500 cursor-pointer min-w-[120px]"
            >
              {modelGroups.length === 0 && <option value="">暂无可用模型</option>}
              {modelGroups.map((group) => (
                <optgroup key={group.provider} label={group.provider}>
                  {group.models.map((m) => (
                    <option key={`${group.provider}:${m.name}`} value={`${group.provider}::${m.name}`}>
                      {m.display_name || m.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            {hasThinking && (
              <div className="flex items-center gap-1.5">
                {/* 思考开关（药丸式） */}
                <button
                  type="button"
                  onClick={() => onThinkingChange(!thinkingEnabled)}
                  className={cn(
                    'flex items-center gap-1 rounded-full border px-2 py-0.5 transition-colors',
                    thinkingEnabled
                      ? 'border-orange-200 bg-orange-50 text-orange-600'
                      : 'border-gray-200 text-gray-400 hover:text-gray-600',
                  )}
                  title={thinkingEnabled ? '已开启思考' : '已关闭思考'}
                >
                  <Brain size={13} />
                  <span>思考</span>
                </button>
                {/* 思考强度（分段控件）：仅开启且模型支持档位时显示 */}
                {thinkingEnabled && showThinkingLevel && (
                  <div className="flex items-center gap-0.5 rounded-full bg-gray-100 p-0.5">
                    {thinkingOptions.map((v) => (
                      <button
                        key={v}
                        type="button"
                        onClick={() => onThinkingLevelChange?.(v as ThinkingLevel)}
                        className={cn(
                          'rounded-full px-2 py-0.5 text-[11px] transition-colors',
                          selectedThinkingLevel === v
                            ? 'bg-white text-gray-800 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700',
                        )}
                      >
                        {THINKING_LEVEL_LABELS[v as ThinkingLevel] || v}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <span className="shrink-0 pl-2">AI 回答可能不准确,请核实关键信息</span>
        </div>
      </div>
    </div>
  );
}
