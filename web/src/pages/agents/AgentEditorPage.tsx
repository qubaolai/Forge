import { useEffect, useReducer, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Bot } from 'lucide-react';
import { agentsApi } from '@/api';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

import { AgentDraft, agentToDraft, createDefaultDraft } from '@/components/agent/draft';
import { BasicTab } from '@/components/agent/BasicTab';
import { ModelTab } from '@/components/agent/ModelTab';
import { PromptTab } from '@/components/agent/PromptTab';
import { KnowledgeTab } from '@/components/agent/KnowledgeTab';
import { ToolsTab } from '@/components/agent/ToolsTab';
import { AdvancedTab } from '@/components/agent/AdvancedTab';
import { PreviewPanel } from '@/components/agent/PreviewPanel';

type TabKey = 'basic' | 'model' | 'prompt' | 'knowledge' | 'tools' | 'advanced';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'basic', label: '基础' },
  { key: 'model', label: '模型' },
  { key: 'prompt', label: 'Prompt' },
  { key: 'knowledge', label: '知识库' },
  { key: 'tools', label: '工具' },
  { key: 'advanced', label: '高级' },
];

// reducer:支持任意层级的部分更新
type DraftAction = { type: 'patch'; payload: Partial<AgentDraft> } | { type: 'reset'; payload: AgentDraft };
function reducer(state: AgentDraft, action: DraftAction): AgentDraft {
  switch (action.type) {
    case 'patch': return { ...state, ...action.payload };
    case 'reset': return action.payload;
  }
}

const DRAFT_KEY = (id: string) => `agent-draft-${id}`;

export default function AgentEditorPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const isNew = !agentId;
  const draftKey = DRAFT_KEY(agentId || 'new');

  const [tab, setTab] = useState<TabKey>('basic');

  // 加载已有 Agent(编辑模式)
  const { data: existing, isLoading } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !isNew,
  });

  // 草稿 state
  const [draft, dispatch] = useReducer(reducer, createDefaultDraft());
  const [draftReady, setDraftReady] = useState(false);

  // 初始化草稿:优先 localStorage,其次后端,最后默认
  useEffect(() => {
    const saved = localStorage.getItem(draftKey);
    if (saved) {
      try {
        dispatch({ type: 'reset', payload: JSON.parse(saved) });
        setDraftReady(true);
        return;
      } catch { /* fallthrough */ }
    }
    if (!isNew && existing) {
      dispatch({ type: 'reset', payload: agentToDraft(existing) });
      setDraftReady(true);
    } else if (isNew) {
      dispatch({ type: 'reset', payload: createDefaultDraft() });
      setDraftReady(true);
    }
  }, [draftKey, isNew, existing]);

  // 防抖保存草稿到 localStorage
  const debouncedDraft = useDebouncedValue(draft, 500);
  useEffect(() => {
    if (!draftReady) return;
    localStorage.setItem(draftKey, JSON.stringify(debouncedDraft));
  }, [debouncedDraft, draftKey, draftReady]);

  function update(patch: Partial<AgentDraft>) {
    dispatch({ type: 'patch', payload: patch });
  }

  // 提交
  const createMutation = useMutation({
    mutationFn: () => agentsApi.create(draft),
    onSuccess: (created) => {
      localStorage.removeItem(draftKey);
      qc.invalidateQueries({ queryKey: ['agents'] });
      navigate(`/agents/${created.id}`, { replace: true });
    },
  });
  const updateMutation = useMutation({
    mutationFn: () => agentsApi.update(agentId!, draft),
    onSuccess: () => {
      localStorage.removeItem(draftKey);
      qc.invalidateQueries({ queryKey: ['agents'] });
      qc.invalidateQueries({ queryKey: ['agent', agentId] });
    },
  });
  const submitting = createMutation.isPending || updateMutation.isPending;

  // 校验:基础字段必填
  const canSubmit = !!draft.name.trim() && !!draft.model.model_id && !submitting;

  function handleSubmit() {
    if (!canSubmit) return;
    if (isNew) createMutation.mutate();
    else updateMutation.mutate();
  }

  async function handleDiscard() {
    if (!await confirm({ message: '丢弃当前修改并恢复原始配置?', confirmLabel: '丢弃', danger: true })) return;
    localStorage.removeItem(draftKey);
    if (isNew) {
      dispatch({ type: 'reset', payload: createDefaultDraft() });
    } else if (existing) {
      dispatch({ type: 'reset', payload: agentToDraft(existing) });
    }
  }

  if (!isNew && isLoading && !draftReady) {
    return <div className="p-6 text-sm text-gray-400">加载中…</div>;
  }

  return (
    <div className="h-full flex flex-col">
      {/* 顶部 */}
      <div className="px-6 py-3 border-b flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/agents')}
            className="text-gray-500 hover:text-gray-900"
          >
            <ArrowLeft size={16} />
          </button>
          <div className="w-7 h-7 rounded-md bg-purple-50 flex items-center justify-center shrink-0">
            <Bot className="text-purple-600" size={14} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">
              {draft.name || (isNew ? '新建 Agent' : 'Agent')}
            </div>
            <div className="text-[11px] text-gray-400">
              {isNew ? '新建' : '编辑'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400">已自动保存草稿</span>
          <button
            onClick={handleDiscard}
            className="px-3 py-1.5 text-xs border rounded-md hover:bg-gray-50"
          >
            丢弃修改
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-3 py-1.5 text-xs bg-black text-white rounded-md
              hover:bg-gray-800 disabled:opacity-50"
          >
            {submitting ? '保存中…' : isNew ? '创建' : '保存'}
          </button>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* 左:表单 */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Tab 栏 */}
          <div className="flex gap-0 border-b px-6 shrink-0">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cn(
                  'px-3 py-2.5 text-sm border-b-2 transition-colors -mb-px',
                  tab === t.key
                    ? 'border-gray-900 text-gray-900 font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-900',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          {/* Tab 内容 */}
          <div className="flex-1 overflow-y-auto p-6">
            {tab === 'basic' && <BasicTab draft={draft} update={update} />}
            {tab === 'model' && <ModelTab draft={draft} update={update} />}
            {tab === 'prompt' && <PromptTab draft={draft} update={update} />}
            {tab === 'knowledge' && <KnowledgeTab draft={draft} update={update} />}
            {tab === 'tools' && <ToolsTab draft={draft} update={update} />}
            {tab === 'advanced' && <AdvancedTab draft={draft} update={update} />}
          </div>
        </div>

        {/* 右:预览 */}
        <div className="w-[340px] border-l shrink-0">
          <PreviewPanel draft={draft} />
        </div>
      </div>
    </div>
  );
}
