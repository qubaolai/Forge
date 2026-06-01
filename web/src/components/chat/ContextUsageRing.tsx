import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { ContextUsage } from '@/types';

/** chat 模式展示的上下文层 (不含工作区与工作流) */
const STANDARD_LAYERS: { name: string; label: string }[] = [
  { name: 'system_prompt', label: '系统提示' },
  { name: 'facts',         label: '记忆事实' },
  { name: 'summary',       label: '对话摘要' },
  { name: 'dialogue',      label: '对话历史' },
  { name: 'tool_results',  label: '工具结果' },
  { name: 'current_input', label: '本轮输入' },
];

function fmtTokens(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
  return String(n);
}

/** 阈值配色: <70% 橙 / 70-90% 琥珀 / >90% 红 */
function ringColor(ratio: number): string {
  if (ratio > 0.9) return '#ef4444'; // red-500
  if (ratio > 0.7) return '#f59e0b'; // amber-500
  return '#f97316'; // orange-500
}

/**
 * 当前会话上下文占用圆环。
 * - 圆环展示总占用百分比 (中心数字)
 * - 点击展开各类型 (层) 明细: 中文层名 + token + 占比%
 */
export function ContextUsageRing({ usage }: { usage: ContextUsage | null }) {
  if (!usage || !usage.context_window) return null;

  const ratio = Math.max(0, Math.min(1, usage.total_ratio || 0));
  const pct = Math.round(ratio * 100);
  const color = ringColor(ratio);

  const size = 30;
  const stroke = 3.5;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - ratio);

  // 始终展示全部 8 个标准层, 无数据时补 0
  const layerMap = new Map((usage.layers || []).map((l) => [l.name, l]));
  const layers = STANDARD_LAYERS.map(({ name, label }) => {
    const d = layerMap.get(name);
    return { name, label, token_count: d?.token_count ?? 0, ratio: d?.ratio ?? 0 };
  });

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="relative flex items-center justify-center outline-none hover:opacity-80 transition-opacity"
          title={`上下文占用 ${pct}%`}
          aria-label={`上下文占用 ${pct}%`}
        >
          <svg width={size} height={size} className="-rotate-90">
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke="#e5e7eb"
              strokeWidth={stroke}
            />
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={color}
              strokeWidth={stroke}
              strokeDasharray={circ}
              strokeDashoffset={offset}
              strokeLinecap="round"
              style={{ transition: 'stroke-dashoffset 0.3s ease' }}
            />
          </svg>
          <span
            className="absolute text-[8px] font-semibold tabular-nums"
            style={{ color }}
          >
            {pct}
          </span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 w-64 rounded-lg border border-gray-200 bg-white p-3 text-sm shadow-lg"
        >
          <div className="mb-1.5 flex items-center justify-between">
            <span className="font-medium text-gray-700">上下文占用</span>
            <span className="text-xs tabular-nums" style={{ color }}>
              {pct}%
            </span>
          </div>
          <div className="mb-2 text-xs tabular-nums text-gray-400">
            {fmtTokens(usage.input_tokens)} / {fmtTokens(usage.context_window)} tokens
          </div>
          <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full"
              style={{ width: `${pct}%`, background: color }}
            />
          </div>
          <div className="space-y-1.5">
            {layers.map((l) => {
              const lpct = Math.round((l.ratio || 0) * 1000) / 10;
              return (
                <div
                  key={l.name}
                  className="flex items-center justify-between text-xs"
                >
                  <span className={l.token_count > 0 ? 'text-gray-600' : 'text-gray-300'}>
                    {l.label}
                  </span>
                  <span className={`tabular-nums ${l.token_count > 0 ? 'text-gray-400' : 'text-gray-300'}`}>
                    {fmtTokens(l.token_count)} · {lpct}%
                  </span>
                </div>
              );
            })}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
