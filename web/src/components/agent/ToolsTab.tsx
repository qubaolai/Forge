import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react';
import { toolsApi } from '@/api';
import { ToolBinding, ToolDefinition } from '@/types';
import { AgentDraft } from './draft';
import { cn } from '@/lib/utils';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

export function ToolsTab({ draft, update }: Props) {
  const { data: tools, isLoading } = useQuery({
    queryKey: ['tools'],
    queryFn: () => toolsApi.list(),
  });

  function getBinding(toolId: string): ToolBinding | undefined {
    return draft.tools.find((t) => t.tool_id === toolId);
  }

  function setBinding(toolId: string, patch: Partial<ToolBinding>) {
    const exists = draft.tools.find((t) => t.tool_id === toolId);
    let next: ToolBinding[];
    if (exists) {
      next = draft.tools.map((t) =>
        t.tool_id === toolId ? { ...t, ...patch } : t,
      );
    } else {
      next = [...draft.tools, { tool_id: toolId, enabled: true, config: {}, ...patch }];
    }
    update({ tools: next });
  }

  if (isLoading) {
    return <div className="text-sm text-gray-400">加载工具列表…</div>;
  }

  if (!tools || tools.length === 0) {
    return <div className="text-sm text-gray-500">暂无可用工具</div>;
  }

  return (
    <div className="max-w-xl">
      <p className="text-xs text-gray-500 mb-3">
        勾选工具让 Agent 在对话中自主调用,展开可配置参数。
      </p>
      <div className="border rounded-md divide-y">
        {tools.map((tool) => (
          <ToolRow
            key={tool.id}
            tool={tool}
            binding={getBinding(tool.id)}
            onToggle={(enabled) => setBinding(tool.id, { enabled })}
            onConfig={(config) => setBinding(tool.id, { config })}
          />
        ))}
      </div>
    </div>
  );
}

function ToolRow({
  tool, binding, onToggle, onConfig,
}: {
  tool: ToolDefinition;
  binding: ToolBinding | undefined;
  onToggle: (enabled: boolean) => void;
  onConfig: (config: Record<string, unknown>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const enabled = !!binding?.enabled;

  // 简单 JSON Schema 渲染:仅支持 type=object 顶层属性
  const properties = (tool.parameters_schema as { properties?: Record<string, { type: string; description?: string }> })?.properties || {};

  return (
    <div>
      <div className="flex items-center gap-3 px-3 py-2.5">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onToggle(e.target.checked)}
          className="rounded"
        />
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex-1 flex items-center gap-2 text-left"
        >
          {Object.keys(properties).length > 0 ? (
            expanded ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />
          ) : (
            <span className="w-3.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-sm font-medium">
              {tool.name}
              {tool.is_dangerous && (
                <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-orange-50 text-orange-700">
                  <AlertTriangle size={9} />
                  危险
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">{tool.description}</div>
          </div>
        </button>
      </div>

      {expanded && Object.keys(properties).length > 0 && (
        <div className={cn(
          'px-10 pb-3 space-y-2 bg-gray-50',
          !enabled && 'opacity-50 pointer-events-none',
        )}>
          {Object.entries(properties).map(([key, schema]) => (
            <div key={key}>
              <label className="block text-[11px] text-gray-600 mb-1">
                {key} <span className="text-gray-400">({schema.type})</span>
              </label>
              <input
                type={schema.type === 'number' ? 'number' : 'text'}
                value={String(binding?.config[key] ?? '')}
                onChange={(e) =>
                  onConfig({
                    ...(binding?.config || {}),
                    [key]: schema.type === 'number' ? Number(e.target.value) : e.target.value,
                  })
                }
                placeholder={schema.description}
                className="w-full text-xs border rounded px-2 py-1 bg-white outline-none focus:border-gray-400"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
