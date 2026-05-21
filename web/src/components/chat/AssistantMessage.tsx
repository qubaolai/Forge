import { useLayoutEffect, useRef, useState } from 'react';
import {
  Copy, RotateCcw, ThumbsUp, ThumbsDown, AlertCircle, Check, Sparkles,
  Brain, ChevronDown, ChevronRight,
} from 'lucide-react';
import { ChatMessage, Citation } from '@/types';
import { MarkdownContent } from './MarkdownContent';
import { ToolCallCard } from './ToolCallCard';

interface Props {
  message: ChatMessage;
  onRegenerate?: () => void;
  onResume?: () => void;
  onCitationClick?: (citation: Citation) => void;
}

export function AssistantMessage({ message, onRegenerate, onResume, onCitationClick }: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const isStreaming = message.status === 'streaming' || message.status === 'pending';
  const isError = message.status === 'error';
  const isResumable = message.status === 'partial' || message.status === 'aborted';
  const hasContent = !!message.content;
  const hasTools = !!(message.tool_calls && message.tool_calls.length > 0);
  const hasReasoning = !!message.reasoning_content;

  return (
    <div className="flex gap-3">
      {/* 头像 */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-orange-400 to-orange-600 text-white shadow-sm">
        <Sparkles size={15} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* 思考框: 流式且无内容时始终显示; 有思考链内容时继续显示 */}
        {((isStreaming && !hasContent) || hasReasoning) && (
          <ReasoningBlock
            content={message.reasoning_content || ''}
            streaming={isStreaming && !hasContent}
            durationMs={message.reasoning_duration_ms}
          />
        )}

        {/* 工具调用过程 (若有) */}
        {hasTools && (
          <div className="flex flex-col gap-1.5">
            {message.tool_calls!.map((tc) => (
              <ToolCallCard key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {/* 主体内容 */}
        {hasContent ? (
          <div className="text-gray-900">
            <MarkdownContent
              content={message.content}
              citations={message.citations}
              onCitationClick={onCitationClick}
            />
            {isStreaming && (
              <span className="inline-block w-[3px] h-4 ml-0.5 bg-gray-700 align-middle animate-pulse rounded-sm" />
            )}
          </div>
        ) : null}

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
              <span className="ml-2 text-[11px] text-gray-400">
                {message.citations.length} 条引用
              </span>
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

function ReasoningBlock({
  content,
  streaming,
  durationMs,
}: {
  content: string;
  streaming: boolean;
  durationMs?: number;
}) {
  // 默认折叠, 用户可手动展开查看思考过程
  const [open, setOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 流式期间随内容增长自动滚到底, 让用户看到最新一行
  useLayoutEffect(() => {
    if (open && streaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, open, streaming]);

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/60 text-[13px]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-gray-500 hover:text-gray-700"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Brain size={13} />
        <span>
          {streaming
            ? '思考中…'
            : durationMs && durationMs > 0
              ? `思考用时 ${formatDuration(durationMs)}`
              : '思考过程'}
        </span>
        {streaming && (
          <span className="ml-1 flex gap-1">
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.3s]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.15s]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400" />
          </span>
        )}
      </button>
      {open && (
        <div
          ref={scrollRef}
          className="border-t border-gray-200 px-3 py-2 text-gray-600 whitespace-pre-wrap leading-[1.7] max-h-[220px] overflow-y-auto"
        >
          {content}
        </div>
      )}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} 毫秒`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} 秒`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return rem === 0 ? `${m} 分钟` : `${m} 分 ${rem} 秒`;
}
