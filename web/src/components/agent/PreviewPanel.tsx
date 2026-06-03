import { Eye, BookOpen, Wrench, Cpu } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { kbApi, modelsApi, toolsApi } from '@/api';
import { AgentDraft } from './draft';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

interface Props {
  draft: AgentDraft;
}

/**
 * 编辑器右侧预览:展示当前配置生成的 Agent 摘要 + 模拟对话气泡。
 * (真正的"和草稿 Agent 聊天"需要后端支持临时会话,后续迭代)
 */
export function PreviewPanel({ draft }: Props) {
  // 防抖:配置改动 1s 后才刷新预览,避免拖滑块时频繁渲染
  const debounced = useDebouncedValue(draft, 800);

  const { data: kbs } = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: () => kbApi.list({ page: 1, page_size: 100 }),
  });
  const { data: models } = useQuery({
    queryKey: ['models'],
    queryFn: () => modelsApi.list(),
  });
  const { data: tools } = useQuery({
    queryKey: ['tools'],
    queryFn: () => toolsApi.list(),
  });

  const selectedModel = models?.find((m) => m.model_id === debounced.model.model_id);
  const selectedKbs = kbs?.items.filter((k) => debounced.retrieval.kb_ids.includes(k.id)) || [];
  const enabledTools = debounced.tools.filter((t) => t.enabled);
  const enabledToolNames = enabledTools
    .map((b) => tools?.find((t) => t.id === b.tool_id)?.name)
    .filter(Boolean);

  return (
    <div className="h-full flex flex-col bg-gray-50/30">
      <div className="px-4 py-3 border-b flex items-center gap-2 shrink-0">
        <Eye size={14} />
        <span className="text-sm font-medium">预览</span>
        <span className="ml-auto text-[11px] text-gray-400">配置摘要</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 配置摘要 */}
        <section className="bg-white rounded-md border p-3 space-y-2 text-xs">
          <SummaryRow icon={<Cpu size={12} />} label="模型">
            {selectedModel ? selectedModel.display_name || selectedModel.name : <span className="text-gray-400">未选择</span>}
            {selectedModel && (
              <span className="text-gray-400 ml-1.5">temp {debounced.model.temperature}</span>
            )}
          </SummaryRow>
          <SummaryRow icon={<BookOpen size={12} />} label="知识库">
            {selectedKbs.length > 0 ? (
              <span className="truncate" title={selectedKbs.map((k) => k.name).join(', ')}>
                {selectedKbs.map((k) => k.name).join(', ')}
              </span>
            ) : (
              <span className="text-gray-400">无</span>
            )}
          </SummaryRow>
          <SummaryRow icon={<Wrench size={12} />} label="工具">
            {enabledToolNames.length > 0 ? (
              enabledToolNames.join(', ')
            ) : (
              <span className="text-gray-400">无</span>
            )}
          </SummaryRow>
        </section>

        {/* 模拟对话 */}
        <section>
          <div className="text-[11px] text-gray-400 mb-2">模拟对话</div>
          <div className="bg-white rounded-md border p-3 space-y-3">
            {debounced.opening_message && (
              <div className="text-xs text-gray-700 leading-relaxed">
                {debounced.opening_message}
              </div>
            )}
            <div className="flex justify-end">
              <div className="max-w-[80%] bg-blue-50 px-3 py-1.5 rounded-2xl rounded-tr-sm text-xs">
                帮我介绍一下产品
              </div>
            </div>
            {enabledToolNames.length > 0 && (
              <div className="bg-gray-50 border rounded px-2.5 py-1.5 text-[11px] text-gray-600">
                <div className="font-medium">调用工具: {enabledToolNames[0]}</div>
              </div>
            )}
            {selectedKbs.length > 0 && (
              <div className="bg-gray-50 border rounded px-2.5 py-1.5 text-[11px] text-gray-600">
                <div className="font-medium">检索: {selectedKbs[0].name}</div>
                <div className="text-gray-400 mt-0.5">top-k={debounced.retrieval.top_k} · 阈值={debounced.retrieval.score_threshold}</div>
              </div>
            )}
            <div className="text-xs text-gray-700 leading-relaxed">
              基于 {selectedKbs.length > 0 ? '知识库' : '已配置'}内容,我可以为您介绍…
              {selectedKbs.length > 0 && (
                <sup className="text-blue-600 ml-0.5">[1]</sup>
              )}
            </div>
            <div className="text-[10px] text-gray-400 italic pt-1 border-t">
              这只是配置效果预览,发布后才能真实对话
            </div>
          </div>
        </section>

        {/* System Prompt 预览 */}
        <section>
          <div className="text-[11px] text-gray-400 mb-2">System Prompt</div>
          <div className="bg-white rounded-md border p-3 text-[11px] font-mono text-gray-700
            whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">
            {debounced.system_prompt || <span className="text-gray-400 font-sans">未填写</span>}
          </div>
        </section>
      </div>
    </div>
  );
}

function SummaryRow({
  icon, label, children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-gray-400">{icon}</span>
      <span className="text-gray-500 w-12 shrink-0">{label}</span>
      <span className="flex-1 truncate text-gray-900">{children}</span>
    </div>
  );
}
