import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { ContextUsage } from '@/types';

/** 上下文层名 -> 中文标签 */
const LAYER_LABELS: Record<string, string> = {
  system_prompt: '系统提示',
  workspace: '工作区',
  facts: '记忆事实',
  summary: '对话摘要',
  dialogue: '对话历史',
  tool_results: '工具结果',
  workflow_step: '工作流步骤',
  current_input: '本轮输入',
};

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

  const layers = (usage.layers || []).filter((l) => l.token_count > 0);

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
            {layers.length === 0 ? (
              <div className="text-xs text-gray-400">暂无分层数据</div>
            ) : (
              layers.map((l) => {
                const lpct = Math.round((l.ratio || 0) * 1000) / 10;
                return (
                  <div
                    key={l.name}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="text-gray-600">
                      {LAYER_LABELS[l.name] || l.name}
                    </span>
                    <span className="tabular-nums text-gray-400">
                      {fmtTokens(l.token_count)} · {lpct}%
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
