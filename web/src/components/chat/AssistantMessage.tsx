import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  Copy, RotateCcw, ThumbsUp, ThumbsDown, AlertCircle, Check, Sparkles,
  ChevronDown, ChevronRight, Package,
} from 'lucide-react';
import { ChatMessage, ChatFileMeta, Citation, ToolCall } from '@/types';
import { cn } from '@/lib/utils';
import { filesApi } from '@/api';
import { MarkdownContent } from './MarkdownContent';
import { FileCard } from './FileCard';

// 仅向用户展示「操作文件 / 查询知识库」类工具; 其他 (read_message / time_tool 等)
// 是内部辅助调用, 用户不关注, 不展示。
const VISIBLE_TOOL_NAMES = new Set(['write_file', 'read_file', 'knowledge_search']);

interface Props {
  message: ChatMessage;
  onRegenerate?: () => void;
  onResume?: () => void;
  onCitationClick?: (citation: Citation) => void;
  onFilePreview?: (file: ChatFileMeta) => void;
}

export function AssistantMessage({ message, onRegenerate, onResume, onCitationClick, onFilePreview }: Props) {
  const [copied, setCopied] = useState(false);
  const [citationsOpen, setCitationsOpen] = useState(false);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);

  // 正文 [N] 角标点击: 展开来源卡片区 + 高亮对应来源, 再透传上层
  function handleCitationClick(c: Citation) {
    setCitationsOpen(true);
    setActiveCitation(c.index);
    onCitationClick?.(c);
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const isStreaming = message.status === 'streaming' || message.status === 'pending';
  const isError = message.status === 'error';
  const isResumable = message.status === 'partial' || message.status === 'aborted';
  const hasContent = !!message.content;
  // 展示用工具列表: 过滤到白名单 (文件操作 / 知识库)
  const visibleTools = (message.tool_calls || []).filter((tc) => VISIBLE_TOOL_NAMES.has(tc.tool_name));
  const hasTools = visibleTools.length > 0;
  const hasReasoning = !!message.reasoning_content;
  const generatedFiles = (message.files || []).filter((f) => f.source === 'generated');

  return (
    <div className="flex gap-3">
      {/* 头像 */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-orange-400 to-orange-600 text-white shadow-sm">
        <Sparkles size={15} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* 思考过程 (纯文字 + 扫光; 工具步骤独立展示) */}
        {(hasReasoning || (isStreaming && !hasContent && !hasTools)) && (
          <ReasoningBlock
            content={message.reasoning_content || ''}
            streaming={isStreaming && !hasContent}
            durationMs={message.reasoning_duration_ms}
          />
        )}

        {/* 工具调用步骤 (纯文字 + 扫光, 仅白名单工具) */}
        {hasTools && <ToolSteps toolCalls={visibleTools} streaming={isStreaming} />}

        {/* 主体内容 */}
        {hasContent ? (
          <div className="text-gray-900">
            <MarkdownContent
              content={message.content}
              citations={message.citations}
              onCitationClick={handleCitationClick}
            />
            {isStreaming && (
              <span className="inline-block w-[3px] h-4 ml-0.5 bg-gray-700 align-middle animate-pulse rounded-sm" />
            )}
          </div>
        ) : null}

        {/* 引用来源卡片 (knowledge_search 命中, 点击高亮 / 跳转原文) */}
        {message.citations && message.citations.length > 0 && (
          <CitationsBlock
            citations={message.citations}
            open={citationsOpen}
            onToggle={() => setCitationsOpen((v) => !v)}
            activeIndex={activeCitation}
            onCitationClick={onCitationClick}
          />
        )}

        {/* 生成的文件卡片 (write_file 产出) */}
        {generatedFiles.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {generatedFiles.length > 1 && message.session_id && (
              <button
                onClick={() => filesApi.downloadArchive(message.id)}
                className="flex items-center gap-1 self-start text-[12px] text-gray-500 transition-colors hover:text-gray-800"
                title="打包下载本条消息生成的全部文件"
              >
                <Package size={13} /> 打包下载 ({generatedFiles.length})
              </button>
            )}
            {generatedFiles.map((f) => (
              <FileCard
                key={f.id}
                file={f}
                onPreview={onFilePreview}
                onDownload={(file) => filesApi.download(file.id, file.name)}
              />
            ))}
          </div>
        )}

        {/* 错误状态 */}
        {isError && (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[14px] text-red-600">
            <AlertCircle size={15} className="mt-0.5 shrink-0" />
            <span>{message.error_message || '生成失败'}</span>
          </div>
        )}

        {/* 操作条: done / aborted / partial 状态显示 */}
        {(message.status === 'done' || isResumable) && hasContent && (
          <div className="flex items-center gap-1 text-xs text-gray-400 -ml-1.5">
            <ActionButton onClick={handleCopy} title="复制">
              {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
            </ActionButton>
            {onRegenerate && (
              <ActionButton onClick={onRegenerate} title="重新生成">
                <RotateCcw size={13} />
              </ActionButton>
            )}
            <ActionButton title="赞">
              <ThumbsUp size={13} />
            </ActionButton>
            <ActionButton title="踩">
              <ThumbsDown size={13} />
            </ActionButton>
            {message.citations && message.citations.length > 0 && (
              <button
                onClick={() => setCitationsOpen((v) => !v)}
                className="ml-2 text-[11px] text-gray-400 transition-colors hover:text-gray-600"
              >
                {message.citations.length} 条引用
              </button>
            )}
            {message.usage?.total_tokens ? (
              <span className="text-[11px] text-gray-300">
                {message.usage.total_tokens} tokens
              </span>
            ) : null}
            {isResumable && onResume && (
              <button
                onClick={onResume}
                className="ml-auto flex items-center gap-1 rounded-md border bg-white px-2 py-1 text-[12px] text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900"
              >
                继续生成
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ActionButton({
  onClick,
  title,
  children,
}: {
  onClick?: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="p-1.5 rounded-md hover:bg-gray-100 hover:text-gray-700 transition-colors"
    >
      {children}
    </button>
  );
}

/** 折叠头部: 纯文字标题 (进行中扫光) + 极简展开箭头, 无装饰图标 */
function CollapsibleHeader({
  title,
  active,
  open,
  canToggle,
  onToggle,
}: {
  title: string;
  active: boolean; // 进行中 → 扫光
  open: boolean;
  canToggle: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      disabled={!canToggle}
      className="flex items-center gap-1 text-[13px] text-gray-400 transition-colors hover:text-gray-600 disabled:cursor-default"
    >
      <span className={active ? 'shimmer-text font-medium' : ''}>{title}</span>
      {canToggle && (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}
    </button>
  );
}

/** 思考过程: 纯文字 + 扫光; 思考中显示"思考中"流光, 完成后"思考 X 秒"可展开原文 */
function ReasoningBlock({
  content,
  streaming,
  durationMs,
}: {
  content: string;
  streaming: boolean;
  durationMs?: number;
}) {
  const hasContent = !!content;
  const [open, setOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (open && streaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, open, streaming]);

  const thinking = streaming && !hasContent;
  let title: string;
  if (thinking) title = '思考中';
  else if (durationMs && durationMs > 0) title = `思考 ${formatDuration(durationMs)}`;
  else title = '思考过程';

  return (
    <div>
      <CollapsibleHeader
        title={title}
        active={thinking}
        open={open}
        canToggle={hasContent}
        onToggle={() => setOpen((v) => !v)}
      />
      {open && hasContent && (
        <div
          ref={scrollRef}
          className="mt-1 max-h-[320px] overflow-y-auto whitespace-pre-wrap border-l-2 border-gray-200 pl-3 text-[13px] leading-[1.7] text-gray-400"
        >
          {content}
        </div>
      )}
    </div>
  );
}

/** 工具调用步骤 (纯文字 + 扫光, 无图标):
 *  - 生成中: 平铺, 每步一行, 进行中的工具文字扫光;
 *  - 已完成: 按工具类型分组聚合(如「写入 2 个文件: a.py, b.py」), 工具多时不刷屏。
 */
function ToolSteps({ toolCalls, streaming }: { toolCalls: ToolCall[]; streaming: boolean }) {
  const [open, setOpen] = useState(true);
  const running = streaming && toolCalls.some((t) => t.status === 'running');
  const title = running ? '正在执行' : `执行了 ${toolCalls.length} 个操作`;

  return (
    <div>
      <CollapsibleHeader
        title={title}
        active={running}
        open={open}
        canToggle
        onToggle={() => setOpen((v) => !v)}
      />
      {open && (
        <div className="mt-1 flex flex-col gap-1 border-l-2 border-gray-200 pl-3 text-[13px]">
          {streaming
            ? toolCalls.map((tc) => <ToolStepLine key={tc.id} tc={tc} />)
            : groupTools(toolCalls).map((g) => <ToolGroupRow key={g.kind} group={g} />)}
        </div>
      )}
    </div>
  );
}

/** 生成中的单步: 进行中扫光, 失败红色, 完成灰色 */
function ToolStepLine({ tc }: { tc: ToolCall }) {
  const running = tc.status === 'running';
  const error = tc.status === 'error';
  return (
    <div className={cn('truncate', running ? 'shimmer-text font-medium' : error ? 'text-red-500' : 'text-gray-500')}>
      {toolActionLabel(tc)}
      {error ? '（失败）' : ''}
    </div>
  );
}

/** 完成后: 单个工具分组一行 (纯文字, 失败红色) */
function ToolGroupRow({ group }: { group: { kind: string; items: ToolCall[] } }) {
  const anyError = group.items.some((t) => t.status === 'error');
  const { label, detail } = groupSummary(group.kind, group.items);
  return (
    <div className={cn('min-w-0', anyError ? 'text-red-500' : 'text-gray-500')}>
      <span>{label}</span>
      {detail && <span className="ml-1.5 text-gray-400">{detail}</span>}
    </div>
  );
}

/** 按 tool_name 分组, 保持首次出现顺序 */
function groupTools(toolCalls: ToolCall[]): { kind: string; items: ToolCall[] }[] {
  const order: string[] = [];
  const map = new Map<string, ToolCall[]>();
  for (const tc of toolCalls) {
    if (!map.has(tc.tool_name)) {
      map.set(tc.tool_name, []);
      order.push(tc.tool_name);
    }
    map.get(tc.tool_name)!.push(tc);
  }
  return order.map((kind) => ({ kind, items: map.get(kind)! }));
}

/** 工具组 → 聚合标签 + 详情 (单个时直接显示对象, 多个时显示计数 + 列表) */
function groupSummary(kind: string, items: ToolCall[]): { label: string; detail: string } {
  const n = items.length;
  if (kind === 'write_file') {
    const names = items.map(filePathOf).filter(Boolean);
    return n === 1
      ? { label: `写入 ${names[0] || '文件'}`, detail: '' }
      : { label: `写入 ${n} 个文件`, detail: names.join('  ·  ') };
  }
  if (kind === 'read_file') {
    const names = items.map(readFileName).filter(Boolean);
    return n === 1
      ? { label: `读取 ${names[0] || '文件'}`, detail: '' }
      : { label: `读取 ${n} 个文件`, detail: names.join('  ·  ') };
  }
  if (kind === 'knowledge_search') {
    const qs = items.map(queryOf).filter(Boolean);
    return n === 1
      ? { label: `检索知识库${qs[0] ? `：${qs[0]}` : ''}`, detail: '' }
      : { label: `检索知识库 ${n} 次`, detail: qs.join('  ·  ') };
  }
  return { label: `调用 ${kind} ${n} 次`, detail: '' };
}

/** 生成中: 单个工具 → 友好中文动作 (read_file 显示读取的文件名) */
function toolActionLabel(tc: ToolCall): string {
  switch (tc.tool_name) {
    case 'write_file':
      return `写入 ${filePathOf(tc) || '文件'}`;
    case 'read_file': {
      const name = readFileName(tc);
      return name ? `读取 ${name}` : '读取文件';
    }
    case 'knowledge_search': {
      const q = queryOf(tc);
      return q ? `检索知识库：${q}` : '检索知识库';
    }
    default:
      return `调用 ${tc.tool_name}`;
  }
}

/** 工具结果可能是 JSON 字符串(后端序列化)或对象, 统一解析成对象 */
function parseToolResult(result: unknown): Record<string, unknown> | null {
  if (!result) return null;
  if (typeof result === 'object') return result as Record<string, unknown>;
  if (typeof result === 'string') {
    try {
      const o = JSON.parse(result);
      return o && typeof o === 'object' ? (o as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
}

function filePathOf(tc: ToolCall): string {
  const args = (tc.arguments || {}) as Record<string, unknown>;
  if (typeof args.path === 'string') return args.path;
  const r = parseToolResult(tc.result);
  return r && typeof r.path === 'string' ? r.path : '';
}

/** read_file 读取的文件名: 优先工具结果里的 filename (running 时可能还没有) */
function readFileName(tc: ToolCall): string {
  const r = parseToolResult(tc.result);
  return r && typeof r.filename === 'string' ? r.filename : '';
}

function queryOf(tc: ToolCall): string {
  const args = (tc.arguments || {}) as Record<string, unknown>;
  const q = typeof args.query === 'string' ? args.query : '';
  return q.length > 24 ? q.slice(0, 24) + '…' : q;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} 毫秒`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} 秒`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return rem === 0 ? `${m} 分钟` : `${m} 分 ${rem} 秒`;
}

// ---------------------------------------------------------------------------
// 引用来源
// ---------------------------------------------------------------------------
function citationPage(c: Citation): number | null {
  const p = (c.metadata as Record<string, unknown> | undefined)?.page;
  return typeof p === 'number' ? p : null;
}

function citationMeta(c: Citation, key: string): string {
  const v = (c.metadata as Record<string, unknown> | undefined)?.[key];
  return typeof v === 'string' ? v : '';
}

/** 引用来源卡片区: 列出 knowledge_search 命中的来源, 点击高亮 / 跳转原文 */
function CitationsBlock({
  citations,
  open,
  onToggle,
  activeIndex,
  onCitationClick,
}: {
  citations: Citation[];
  open: boolean;
  onToggle: () => void;
  activeIndex: number | null;
  onCitationClick?: (c: Citation) => void;
}) {
  return (
    <div>
      <CollapsibleHeader
        title={`${citations.length} 条引用来源`}
        active={false}
        open={open}
        canToggle
        onToggle={onToggle}
      />
      {open && (
        <div className="mt-1 flex flex-col gap-1.5 border-l-2 border-gray-200 pl-3">
          {citations.map((c) => (
            <CitationCard
              key={`${c.chunk_id}-${c.index}`}
              citation={c}
              active={activeIndex === c.index}
              onClick={() => {
                const url = citationMeta(c, 'source_url');
                if (url) window.open(url, '_blank', 'noopener');
                onCitationClick?.(c);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CitationCard({
  citation,
  active,
  onClick,
}: {
  citation: Citation;
  active: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (active && ref.current) {
      ref.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [active]);
  const page = citationPage(citation);
  const kbName = citationMeta(citation, 'kb_name');
  return (
    <button
      ref={ref}
      onClick={onClick}
      className={cn(
        'rounded-md border px-2.5 py-1.5 text-left text-[12px] transition-colors',
        active ? 'border-orange-300 bg-orange-50' : 'border-gray-200 hover:bg-gray-50',
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="rounded bg-gray-100 px-1 text-[10px] text-gray-500">[{citation.index}]</span>
        <span className="truncate font-medium text-gray-700">{citation.document_name || '未命名文档'}</span>
        {page != null && <span className="shrink-0 text-gray-400">第 {page} 页</span>}
        <span className="ml-auto shrink-0 text-gray-300">{citation.score.toFixed(3)}</span>
      </div>
      {kbName && <div className="mt-0.5 text-[11px] text-gray-400">{kbName}</div>}
      <p className="mt-0.5 line-clamp-2 leading-5 text-gray-500">{citation.content}</p>
    </button>
  );
}
