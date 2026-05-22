import { useLayoutEffect, useRef, useState } from 'react';
import {
  Copy, RotateCcw, ThumbsUp, ThumbsDown, AlertCircle, Check, Sparkles,
  Brain, ChevronDown, ChevronRight,
  Loader2, CheckCircle2, XCircle, Wrench,
} from 'lucide-react';
import { AdaptiveRunSummary, ChatMessage, Citation, RunStatus, ToolCall } from '@/types';
import { cn } from '@/lib/utils';
import { MarkdownContent } from './MarkdownContent';

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
  const hasAdaptiveRun = !!message.adaptive_run;

  return (
    <div className="flex gap-3">
      {/* 头像 */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-orange-400 to-orange-600 text-white shadow-sm">
        <Sparkles size={15} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* 思考+工具调用框: 流式无内容 / 有推理链 / 有工具调用时显示 */}
        {((isStreaming && !hasContent) || hasReasoning || hasTools) && (
          <ReasoningBlock
            content={message.reasoning_content || ''}
            streaming={isStreaming && !hasContent}
            durationMs={message.reasoning_duration_ms}
            toolCalls={message.tool_calls}
          />
        )}

        {/* 主体内容 */}
        {hasAdaptiveRun && message.adaptive_run ? (
          <AdaptiveRunBlock run={message.adaptive_run} streaming={isStreaming} />
        ) : hasContent ? (
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

function AdaptiveRunBlock({ run, streaming }: { run: AdaptiveRunSummary; streaming: boolean }) {
  const status = run.status || 'created';
  const events = run.events || [];
  const lastEvents = events.slice(-8);
  const taskEvents = events.filter((evt) => evt.type.startsWith('task.'));
  const artifactEvents = events.filter((evt) => evt.type === 'artifact.created');

  return (
    <div className="overflow-hidden rounded-xl border border-orange-200 bg-gradient-to-br from-orange-50 to-white">
      <div className="flex items-start justify-between gap-3 border-b border-orange-100 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
            <Wrench size={15} className="text-orange-600" />
            <span>Adaptive Run</span>
            <StatusPill status={status} streaming={streaming} />
          </div>
          {run.run_id && (
            <div className="mt-1 truncate font-mono text-[11px] text-gray-400">
              {run.run_id}
            </div>
          )}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
          <Metric label="事件" value={events.length} />
          <Metric label="任务" value={taskEvents.length} />
          <Metric label="产物" value={run.artifact_ids.length || artifactEvents.length} />
        </div>
      </div>

      <div className="px-4 py-3">
        {lastEvents.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 size={14} className="animate-spin" />
            等待后端事件…
          </div>
        ) : (
          <div className="space-y-2">
            {lastEvents.map((evt) => (
              <div key={evt.id} className="flex gap-2 text-[13px]">
                <EventDot type={evt.type} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-gray-700">{adaptiveEventLabel(evt.type)}</span>
                    <span className="text-[11px] text-gray-300">{formatClock(evt.ts)}</span>
                  </div>
                  <div className="mt-0.5 truncate text-gray-500">
                    {adaptivePayloadText(evt.payload)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status, streaming }: { status: RunStatus; streaming: boolean }) {
  const isActive = streaming && !['completed', 'failed', 'blocked', 'aborted'].includes(status);
  const tone =
    status === 'completed'
      ? 'bg-emerald-100 text-emerald-700'
      : status === 'failed' || status === 'blocked' || status === 'aborted'
        ? 'bg-red-100 text-red-700'
        : 'bg-blue-100 text-blue-700';
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px]', tone)}>
      {isActive ? <Loader2 size={11} className="animate-spin" /> : status === 'completed' ? <CheckCircle2 size={11} /> : status === 'failed' ? <XCircle size={11} /> : null}
      {runStatusLabel(status)}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-12 rounded-lg bg-white/70 px-2 py-1">
      <div className="font-medium text-gray-800">{value}</div>
      <div className="text-gray-400">{label}</div>
    </div>
  );
}

function EventDot({ type }: { type: string }) {
  const cls = type.includes('failed') || type.includes('blocked') || type.includes('conflict')
    ? 'bg-red-500'
    : type.includes('completed') || type.includes('passed')
      ? 'bg-emerald-500'
      : 'bg-orange-500';
  return <span className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', cls)} />;
}

function adaptiveEventLabel(type: string): string {
  const labels: Record<string, string> = {
    'run.created': '创建运行',
    'run.started': '启动运行',
    'run.status_changed': '状态变更',
    'run.completed': '运行完成',
    'run.failed': '运行失败',
    'run.blocked': '运行阻塞',
    'task.started': '任务开始',
    'task.completed': '任务完成',
    'task.failed': '任务失败',
    'task.skipped': '任务跳过',
    'wave.started': 'Wave 开始',
    'wave.completed': 'Wave 完成',
    'artifact.created': '产物创建',
    'plan.created': '计划创建',
    'plan.validated': '计划通过',
    'plan.rejected': '计划拒绝',
    'integration.started': '开始集成',
    'integration.completed': '集成完成',
    'integration.conflict': '集成冲突',
    'verify.started': '开始验证',
    'verify.passed': '验证通过',
    'verify.failed': '验证失败',
  };
  return labels[type] || type;
}

function adaptivePayloadText(payload: Record<string, unknown>): string {
  const parts = [
    typeof payload.task_id === 'string' ? `任务 ${payload.task_id}` : '',
    typeof payload.kind === 'string' ? payload.kind : '',
    typeof payload.to_status === 'string' ? `→ ${runStatusLabel(payload.to_status as RunStatus)}` : '',
    typeof payload.artifact_id === 'string' ? `产物 ${payload.artifact_id}` : '',
    typeof payload.reason === 'string' ? `原因 ${payload.reason}` : '',
  ].filter(Boolean);
  return parts.join(' · ') || '已收到事件';
}

function runStatusLabel(status: RunStatus): string {
  const labels: Record<RunStatus, string> = {
    created: '已创建',
    planning: '规划中',
    validating: '校验中',
    executing: '执行中',
    integrating: '集成中',
    verifying: '验证中',
    completed: '已完成',
    failed: '失败',
    blocked: '阻塞',
    aborted: '已中止',
  };
  return labels[status] || status;
}

function formatClock(ts: string): string {
  const date = new Date(ts);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
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
  toolCalls,
}: {
  content: string;
  streaming: boolean;
  durationMs?: number;
  toolCalls?: ToolCall[];
}) {
  const hasContent = !!content;
  const hasTools = !!(toolCalls && toolCalls.length > 0);
  // 有工具调用时默认展开，让用户看到进度
  const initOpen = hasTools;
  const [open, setOpen] = useState(initOpen);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 流式期间随内容增长自动滚到底, 让用户看到最新一行
  useLayoutEffect(() => {
    if (open && streaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, toolCalls, open, streaming]);

  // 标题文本
  let title: string;
  if (streaming) {
    title = hasTools ? '处理中…' : '思考中…';
  } else if (durationMs && durationMs > 0) {
    title = `思考用时 ${formatDuration(durationMs)}`;
  } else if (hasTools && !hasContent) {
    title = `已调用 ${toolCalls!.length} 个工具`;
  } else {
    title = '思考过程';
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/60 text-[13px]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-gray-500 hover:text-gray-700"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Brain size={13} />
        <span>{title}</span>
        {streaming && (
          <span className="ml-1 flex gap-1">
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.3s]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.15s]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400" />
          </span>
        )}
        {/* 工具计数 */}
        {hasTools && !streaming && (
          <span className="ml-auto text-[11px] text-gray-400">
            {toolCalls!.filter(tc => tc.status === 'running').length > 0
              ? `${toolCalls!.filter(tc => tc.status === 'running').length} 个执行中`
              : `${toolCalls!.length} 个工具`}
          </span>
        )}
      </button>
      {open && (
        <div
          ref={scrollRef}
          className="border-t border-gray-200 px-3 py-2 text-gray-600 max-h-[360px] overflow-y-auto"
        >
          {/* 推理文本 */}
          {hasContent && (
            <div className="whitespace-pre-wrap leading-[1.7] mb-2">{content}</div>
          )}

          {/* 工具调用列表 */}
          {hasTools && (
            <div className={cn('space-y-1', hasContent && 'border-t border-gray-200 pt-2')}>
              {toolCalls!.map((tc) => (
                <InlineToolEntry key={tc.id} toolCall={tc} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 思考块内部的紧凑工具条目 */
function InlineToolEntry({ toolCall }: { toolCall: ToolCall }) {
  const [open, setOpen] = useState(false);
  const isRunning = toolCall.status === 'running';
  const isError = toolCall.status === 'error';

  const argsStr =
    toolCall.arguments && Object.keys(toolCall.arguments).length > 0
      ? JSON.stringify(toolCall.arguments, null, 2)
      : '';
  const resultStr = formatInlineResult(toolCall.result);

  return (
    <div className="text-[12px]">
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'w-full flex items-center gap-1.5 px-2 py-1 rounded text-left hover:bg-black/[0.04] transition-colors',
          isError ? 'text-red-600' : isRunning ? 'text-blue-600' : 'text-gray-600',
        )}
      >
        <StatusIcon status={toolCall.status} />
        <code className="text-[11px] px-1 py-0.5 rounded bg-white border border-gray-200 font-medium text-gray-700">
          {toolCall.tool_name}
        </code>
        <span className="text-gray-400 truncate">
          {isRunning ? '调用中' : isError ? '失败' : '完成'}
        </span>
        {!open && argsStr && (
          <span className="ml-auto text-[10px] text-gray-300 truncate max-w-[40%]">
            {argsStr.replace(/\s+/g, ' ').slice(0, 50)}
          </span>
        )}
      </button>

      {/* 展开详情 */}
      {open && (argsStr || resultStr || (isError && toolCall.error_message)) && (
        <div className="ml-6 px-2 pb-2 space-y-1.5">
          {argsStr && (
            <div>
              <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-0.5">参数</div>
              <pre className="text-[11px] font-mono bg-white border border-gray-200 rounded p-1.5 overflow-x-auto leading-relaxed max-h-[120px] overflow-y-auto">
                {argsStr}
              </pre>
            </div>
          )}
          {toolCall.status === 'success' && resultStr && (
            <div>
              <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-0.5">结果</div>
              <pre className="text-[11px] font-mono bg-white border border-gray-200 rounded p-1.5 overflow-x-auto whitespace-pre-wrap max-h-[160px] overflow-y-auto leading-relaxed">
                {resultStr}
              </pre>
            </div>
          )}
          {isError && (toolCall.error_message || resultStr) && (
            <div>
              <div className="text-[10px] text-red-400 uppercase tracking-wider mb-0.5">错误</div>
              <pre className="text-[11px] font-mono bg-white border border-red-200 text-red-600 rounded p-1.5 overflow-x-auto whitespace-pre-wrap max-h-[160px] overflow-y-auto leading-relaxed">
                {toolCall.error_message || resultStr}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ToolCall['status'] }) {
  if (status === 'running') {
    return <Loader2 size={12} className="text-blue-500 animate-spin shrink-0" />;
  }
  if (status === 'error') {
    return <XCircle size={12} className="text-red-500 shrink-0" />;
  }
  if (status === 'success') {
    return <CheckCircle2 size={12} className="text-emerald-500 shrink-0" />;
  }
  return <Wrench size={12} className="text-gray-400 shrink-0" />;
}

function formatInlineResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') {
    const t = result.trim();
    if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
      try {
        return JSON.stringify(JSON.parse(t), null, 2);
      } catch {
        /* not valid json, fall through */
      }
    }
    return result;
  }
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} 毫秒`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} 秒`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return rem === 0 ? `${m} 分钟` : `${m} 分 ${rem} 秒`;
}
