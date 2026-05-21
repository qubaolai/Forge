import { useState } from 'react';
import { ChevronRight, Loader2, CheckCircle2, XCircle, Wrench } from 'lucide-react';
import { ToolCall } from '@/types';
import { cn } from '@/lib/utils';

export function ToolCallCard({ toolCall }: { toolCall: ToolCall }) {
  const [open, setOpen] = useState(false);

  const isRunning = toolCall.status === 'running';
  const isError = toolCall.status === 'error';
  const isSuccess = toolCall.status === 'success';

  const argsStr =
    toolCall.arguments && Object.keys(toolCall.arguments).length > 0
      ? JSON.stringify(toolCall.arguments, null, 2)
      : '';
  const resultStr = formatResult(toolCall.result);

  return (
    <div
      className={cn(
        'border rounded-lg overflow-hidden text-xs transition-colors',
        isError
          ? 'border-red-200 bg-red-50/50'
          : isRunning
            ? 'border-blue-200 bg-blue-50/40'
            : 'border-gray-200 bg-gray-50/60',
      )}
    >
      {/* 头部: 可点击展开 */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-black/[0.02] transition-colors"
      >
        <ChevronRight
          size={12}
          className={cn('text-gray-400 transition-transform shrink-0', open && 'rotate-90')}
        />
        <StatusIcon status={toolCall.status} />
        <span className="font-medium text-gray-800">
          {isRunning ? '调用中' : isError ? '调用失败' : '已调用'}
        </span>
        <code className="text-[11px] text-gray-500 bg-white border border-gray-200 rounded px-1.5 py-0.5">
          {toolCall.tool_name}
        </code>
        {!open && argsStr && (
          <span className="ml-auto text-[11px] text-gray-400 truncate max-w-[40%]">
            {argsStr.replace(/\s+/g, ' ').slice(0, 60)}
          </span>
        )}
      </button>

      {/* 展开内容 */}
      {open && (argsStr || resultStr || (isError && toolCall.error_message)) && (
        <div className="px-3 pb-2.5 pt-1 space-y-2 border-t border-black/[0.06]">
          {argsStr && (
            <Section label="参数">
              <pre className="text-[11px] font-mono bg-white border border-gray-200 rounded p-2 overflow-x-auto leading-relaxed">
                {argsStr}
              </pre>
            </Section>
          )}
          {isSuccess && resultStr && (
            <Section label="结果">
              <pre className="text-[11px] font-mono bg-white border border-gray-200 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-60 leading-relaxed">
                {resultStr}
              </pre>
            </Section>
          )}
          {isError && (toolCall.error_message || resultStr) && (
            <Section label="错误">
              <pre className="text-[11px] font-mono bg-white border border-red-200 text-red-600 rounded p-2 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                {toolCall.error_message || resultStr}
              </pre>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ToolCall['status'] }) {
  if (status === 'running') {
    return <Loader2 size={13} className="text-blue-500 animate-spin shrink-0" />;
  }
  if (status === 'error') {
    return <XCircle size={13} className="text-red-500 shrink-0" />;
  }
  if (status === 'success') {
    return <CheckCircle2 size={13} className="text-emerald-500 shrink-0" />;
  }
  return <Wrench size={13} className="text-gray-400 shrink-0" />;
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">{label}</div>
      {children}
    </div>
  );
}

function formatResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') {
    // 尝试美化 JSON 字符串
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
