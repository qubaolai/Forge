import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Brain, Database, Edit2, KeyRound, Layers, Loader2, Plus, RefreshCw,
  Search, Server, ShieldCheck, Trash2,
} from 'lucide-react';
import {
  providersApi, modelsAdminApi, providerKeysApi, modelBindingsApi, ragIndexAdminApi,
} from '@/api';
import {
  ApiError, ModelUpsert, ProviderAdmin, ProviderKey, ProviderModel, RagIndexStatus,
  SystemModelBinding,
} from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';

const PROVIDERS_KEY = ['admin-providers'];
const inputCls =
  'w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-orange-400';
const THINKING_LEVELS = ['low', 'medium', 'high', 'xhigh'] as const;
type ThinkingLevel = (typeof THINKING_LEVELS)[number];
type ModelTypeFilter = ProviderModel['model_type'] | 'all';
type ModelStatusFilter = 'enabled' | 'disabled' | 'all';
type AdminModelItem = ProviderModel & {
  providerName: string;
  providerEnabled: boolean;
  providerBaseUrl: string | null;
  keyCount: number;
};

const ROLE_ORDER: SystemModelBinding['role'][] = [
  'rag_embedding',
  'semantic_history_embedding',
  'rag_reranker',
];

function isThinkingLevel(value: string): value is ThinkingLevel {
  return THINKING_LEVELS.includes(value as ThinkingLevel);
}

export default function AdminModelsPage() {
  const [typeFilter, setTypeFilter] = useState<ModelTypeFilter>('all');
  const [providerFilter, setProviderFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<ModelStatusFilter>('all');
  const [createProviderSelection, setCreateProviderSelection] = useState('');
  const [creatingProviderName, setCreatingProviderName] = useState('');
  const [editingModel, setEditingModel] = useState<ProviderModel | null>(null);

  const { data: providers, isLoading } = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: () => providersApi.listAdmin(),
  });
  const { data: bindings } = useQuery({
    queryKey: ['admin-model-bindings'],
    queryFn: modelBindingsApi.list,
  });
  const { data: ragStatus } = useQuery({
    queryKey: ['admin-rag-index-status'],
    queryFn: ragIndexAdminApi.status,
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs || [];
      return jobs.some((job) => job.status === 'pending' || job.status === 'running') ? 2000 : false;
    },
  });
  const providerList = providers || [];
  const createProviderName = createProviderSelection || providerList.find((p) => p.is_enabled)?.name || providerList[0]?.name || '';
  const modelItems: AdminModelItem[] = providerList.flatMap((provider) =>
    provider.models.map((model) => ({
      ...model,
      providerName: provider.name,
      providerEnabled: provider.is_enabled,
      providerBaseUrl: provider.base_url,
      keyCount: provider.key_count,
    })),
  );
  const filteredModels = modelItems.filter((model) => {
    if (typeFilter !== 'all' && model.model_type !== typeFilter) return false;
    if (providerFilter !== 'all' && model.providerName !== providerFilter) return false;
    if (statusFilter === 'enabled' && !model.is_enabled) return false;
    if (statusFilter === 'disabled' && model.is_enabled) return false;
    return true;
  });
  const stats = {
    total: modelItems.length,
    chat: modelItems.filter((m) => m.model_type === 'chat').length,
    embedding: modelItems.filter((m) => m.model_type === 'embedding').length,
    reranker: modelItems.filter((m) => m.model_type === 'reranker').length,
    enabled: modelItems.filter((m) => m.is_enabled).length,
  };

  return (
    <div className="h-full overflow-y-auto bg-gray-50/40">
      <div className="mx-auto max-w-7xl px-8 py-8">
        <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-orange-50 px-2.5 py-1 text-xs text-orange-700">
              <ShieldCheck size={13} />
              系统模型运行时配置
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">模型配置</h1>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              统一管理 Chat、Embedding、Reranker 模型，并为 RAG 与语义历史选择当前系统绑定模型。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-white p-2 shadow-sm">
            <select
              value={createProviderName}
              onChange={(e) => setCreateProviderSelection(e.target.value)}
              disabled={providerList.length === 0}
              className={cn(inputCls, 'w-44 bg-white')}
            >
              {providerList.length === 0 ? (
                <option value="">无供应商</option>
              ) : providerList.map((provider) => (
                <option key={provider.id} value={provider.name}>
                  {provider.name}
                </option>
              ))}
            </select>
            <button
              onClick={() => setCreatingProviderName(createProviderName)}
              disabled={!createProviderName}
              className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-1.5 text-sm text-white hover:bg-gray-800 disabled:opacity-40"
            >
              <Plus size={14} />
              新增模型
            </button>
          </div>
        </div>

        <StatsStrip stats={stats} />

        <SystemBindings
          providers={providerList}
          bindings={bindings || []}
          ragStatus={ragStatus}
        />

        <ModelInventory
          models={filteredModels}
          providers={providerList}
          bindings={bindings || []}
          isLoading={isLoading}
          typeFilter={typeFilter}
          providerFilter={providerFilter}
          statusFilter={statusFilter}
          onTypeFilterChange={setTypeFilter}
          onProviderFilterChange={setProviderFilter}
          onStatusFilterChange={setStatusFilter}
          onEdit={setEditingModel}
        />

        <ProviderAccessPanel
          providers={providerList}
          onCreateModel={(providerName) => setCreatingProviderName(providerName)}
        />

        {(creatingProviderName || editingModel) && (
          <ModelDialog
            providerName={editingModel ? '' : creatingProviderName}
            model={editingModel}
            onClose={() => {
              setCreatingProviderName('');
              setEditingModel(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function roleLabel(role: SystemModelBinding['role']) {
  return {
    rag_embedding: 'RAG Embedding',
    semantic_history_embedding: '语义历史 Embedding',
    rag_reranker: 'RAG Reranker',
  }[role];
}

function roleDescription(role: SystemModelBinding['role']) {
  return {
    rag_embedding: '知识库向量入库与向量召回使用的 Embedding 模型',
    semantic_history_embedding: '上下文语义历史捞取使用的 Embedding 模型',
    rag_reranker: 'RAG 检索结果二次排序使用的 Reranker 模型',
  }[role];
}

function roleIcon(role: SystemModelBinding['role']) {
  if (role === 'rag_embedding') return <Database size={18} />;
  if (role === 'semantic_history_embedding') return <Brain size={18} />;
  return <Layers size={18} />;
}

function modelTypeLabel(type: ProviderModel['model_type']) {
  return {
    chat: 'Chat',
    embedding: 'Embedding',
    reranker: 'Reranker',
  }[type];
}

function modelTypeTone(type: ProviderModel['model_type']) {
  return {
    chat: 'bg-blue-50 text-blue-700 border-blue-100',
    embedding: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    reranker: 'bg-purple-50 text-purple-700 border-purple-100',
  }[type];
}

function fmtNumber(value: unknown) {
  const num = Number(value || 0);
  return num > 0 ? num.toLocaleString() : '-';
}

function StatsStrip({ stats }: { stats: { total: number; chat: number; embedding: number; reranker: number; enabled: number } }) {
  const items = [
    { label: '全部模型', value: stats.total, hint: `${stats.enabled} 个已启用` },
    { label: 'Chat', value: stats.chat, hint: '文本与多模态对话' },
    { label: 'Embedding', value: stats.embedding, hint: 'RAG / 语义历史' },
    { label: 'Reranker', value: stats.reranker, hint: '检索重排' },
  ];
  return (
    <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-lg border bg-white px-4 py-3 shadow-sm">
          <div className="text-xs text-gray-500">{item.label}</div>
          <div className="mt-1 text-2xl font-semibold">{item.value}</div>
          <div className="mt-1 text-xs text-gray-400">{item.hint}</div>
        </div>
      ))}
    </div>
  );
}

function SystemBindings({
  providers,
  bindings,
  ragStatus,
}: {
  providers: ProviderAdmin[];
  bindings: SystemModelBinding[];
  ragStatus?: RagIndexStatus;
}) {
  const qc = useQueryClient();
  const models = providers
    .filter((provider) => provider.is_enabled)
    .flatMap((provider) => provider.models.map((model) => ({
      ...model,
      providerName: provider.name,
    })));
  const staleCount = ragStatus?.documents?.stale || 0;
  const readyCount = ragStatus?.documents?.ready || 0;
  const rebuildingCount = ragStatus?.documents?.rebuilding || 0;
  const failedCount = ragStatus?.documents?.failed || 0;
  const affectedCount = Object.values(ragStatus?.documents || {}).reduce((sum, count) => sum + (count || 0), 0);
  const latestJob = ragStatus?.jobs?.[0];
  const update = useMutation({
    mutationFn: ({ role, modelId }: { role: string; modelId: string | null }) =>
      modelBindingsApi.update(role, modelId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-model-bindings'] });
      qc.invalidateQueries({ queryKey: ['admin-rag-index-status'] });
      toast.success('系统模型绑定已更新');
    },
    onError: (e) => toast.error((e as ApiError).message || '切换失败'),
  });
  const rebuild = useMutation({
    mutationFn: ragIndexAdminApi.rebuild,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-rag-index-status'] });
      toast.success('已提交 RAG 索引重建任务');
    },
    onError: (e) => toast.error((e as ApiError).message || '提交重建失败'),
  });
  const retry = useMutation({
    mutationFn: (jobId: string) => ragIndexAdminApi.retry(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-rag-index-status'] });
      toast.success('已提交失败文档重试任务');
    },
    onError: (e) => toast.error((e as ApiError).message || '提交重试失败'),
  });

  return (
    <section className="mb-6">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">系统当前使用模型</h2>
          <p className="mt-0.5 text-xs text-gray-500">
            这些绑定会被 RAG、语义历史和重排运行时实时解析；RAG Embedding 切换后需要重建向量索引。
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      {ROLE_ORDER.map((role) => {
        const binding = bindings.find((item) => item.role === role);
        const expected = role === 'rag_reranker' ? 'reranker' : 'embedding';
        const candidates = models.filter((m) => m.model_type === expected && m.is_enabled);
        const current = binding
          ? models.find((m) => m.model_id === binding.model_id)
          : undefined;
        return (
          <div key={role} className="rounded-xl border bg-white p-4 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-white">
                {roleIcon(role)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-semibold">{roleLabel(role)}</div>
                  {binding && (
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500">
                      v{binding.version}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{roleDescription(role)}</p>
              </div>
            </div>
            <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-gray-400">当前绑定</div>
              <div className="mt-1 truncate text-sm font-medium">
                {current ? (
                  <>
                    {current.providerName} / {current.display_name || current.name}
                  </>
                ) : (
                  <span className="text-amber-600">未配置</span>
                )}
              </div>
            </div>
            <select
              value={binding?.model_id || ''}
              disabled={!binding}
              onChange={async (e) => {
                const modelId = e.target.value || null;
                if (!binding) return;
                if (role === 'rag_embedding' && binding.model_id && modelId !== binding.model_id) {
                  const ok = await confirm({
                    message: `切换 RAG Embedding 会使 ${affectedCount} 个现有文档的向量索引失效，重建前将仅使用 BM25 检索。是否继续？`,
                    confirmLabel: '继续切换',
                    danger: true,
                  });
                  if (!ok) return;
                }
                update.mutate({ role: binding.role, modelId });
              }}
              className={cn(inputCls, 'mt-2 bg-white')}
            >
              {binding?.optional && <option value="">关闭</option>}
              {!binding?.optional && <option value="">请选择模型</option>}
              {candidates.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.providerName} / {m.display_name || m.name}
                </option>
              ))}
            </select>
            {role === 'rag_embedding' && (
              <div className="mt-3 space-y-2 text-xs">
                <div className="grid grid-cols-4 gap-1.5">
                  <IndexPill label="ready" value={readyCount} />
                  <IndexPill label="stale" value={staleCount} warn />
                  <IndexPill label="rebuild" value={rebuildingCount} />
                  <IndexPill label="failed" value={failedCount} danger />
                </div>
                <div className="flex items-center justify-between pt-1">
                  <span className={staleCount > 0 ? 'text-amber-600' : 'text-gray-400'}>
                    {staleCount > 0 ? '需要重建向量索引' : '向量索引状态正常'}
                  </span>
                  <button
                    onClick={() => rebuild.mutate()}
                    disabled={!binding?.model_id || rebuild.isPending}
                    className="inline-flex items-center gap-1 text-orange-600 disabled:text-gray-300"
                  >
                    <RefreshCw size={12} /> 重建索引
                  </button>
                </div>
                {latestJob && (
                  <div className="rounded bg-gray-50 px-2 py-1.5 text-gray-500">
                    <div className="flex items-center justify-between">
                      <span>最近任务：{latestJob.status}</span>
                      <span>{latestJob.succeeded_documents}/{latestJob.total_documents}</span>
                    </div>
                    {latestJob.failed_documents > 0 && (
                      <div className="mt-1 flex items-center justify-between text-red-500">
                        <span>{latestJob.failed_documents} 个文档失败</span>
                        <button
                          onClick={() => retry.mutate(latestJob.id)}
                          disabled={retry.isPending}
                          className="text-orange-600 disabled:text-gray-300"
                        >
                          重试失败文档
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      </div>
    </section>
  );
}

function IndexPill({
  label,
  value,
  warn,
  danger,
}: {
  label: string;
  value: number;
  warn?: boolean;
  danger?: boolean;
}) {
  return (
    <div className={cn(
      'rounded-md px-2 py-1 text-center',
      danger ? 'bg-red-50 text-red-600' : warn ? 'bg-amber-50 text-amber-700' : 'bg-gray-50 text-gray-500',
    )}>
      <div className="text-sm font-semibold">{value}</div>
      <div className="text-[10px]">{label}</div>
    </div>
  );
}

function ModelInventory({
  models,
  providers,
  bindings,
  isLoading,
  typeFilter,
  providerFilter,
  statusFilter,
  onTypeFilterChange,
  onProviderFilterChange,
  onStatusFilterChange,
  onEdit,
}: {
  models: AdminModelItem[];
  providers: ProviderAdmin[];
  bindings: SystemModelBinding[];
  isLoading: boolean;
  typeFilter: ModelTypeFilter;
  providerFilter: string;
  statusFilter: ModelStatusFilter;
  onTypeFilterChange: (value: ModelTypeFilter) => void;
  onProviderFilterChange: (value: string) => void;
  onStatusFilterChange: (value: ModelStatusFilter) => void;
  onEdit: (model: ProviderModel) => void;
}) {
  return (
    <section className="mb-6 rounded-xl border bg-white shadow-sm">
      <div className="flex flex-col gap-3 border-b px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">模型清单</h2>
          <p className="mt-0.5 text-xs text-gray-500">统一模型注册表，按调用类型维护不同配置。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <select
              value={typeFilter}
              onChange={(e) => onTypeFilterChange(e.target.value as ModelTypeFilter)}
              className="rounded-md border bg-white py-1.5 pl-8 pr-8 text-sm outline-none focus:border-orange-400"
            >
              <option value="all">全部类型</option>
              <option value="chat">Chat</option>
              <option value="embedding">Embedding</option>
              <option value="reranker">Reranker</option>
            </select>
          </div>
          <select
            value={providerFilter}
            onChange={(e) => onProviderFilterChange(e.target.value)}
            className={cn(inputCls, 'w-40 bg-white')}
          >
            <option value="all">全部供应商</option>
            {providers.map((provider) => (
              <option key={provider.id} value={provider.name}>{provider.name}</option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => onStatusFilterChange(e.target.value as ModelStatusFilter)}
            className={cn(inputCls, 'w-32 bg-white')}
          >
            <option value="all">全部状态</option>
            <option value="enabled">已启用</option>
            <option value="disabled">已禁用</option>
          </select>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-500">
            <tr>
              <th className="px-5 py-3 text-left font-medium">模型</th>
              <th className="px-4 py-3 text-left font-medium">类型</th>
              <th className="px-4 py-3 text-left font-medium">系统用途</th>
              <th className="px-4 py-3 text-left font-medium">关键配置</th>
              <th className="px-4 py-3 text-left font-medium">状态</th>
              <th className="w-32 px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr>
                <td colSpan={6} className="py-14 text-center text-sm text-gray-400">加载中…</td>
              </tr>
            )}
            {!isLoading && models.length === 0 && (
              <tr>
                <td colSpan={6} className="py-14 text-center text-sm text-gray-400">没有符合筛选条件的模型</td>
              </tr>
            )}
            {models.map((model) => (
              <ModelInventoryRow
                key={model.model_id}
                model={model}
                bindings={bindings}
                onEdit={() => onEdit(model)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ModelInventoryRow({
  model,
  bindings,
  onEdit,
}: {
  model: AdminModelItem;
  bindings: SystemModelBinding[];
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
    qc.invalidateQueries({ queryKey: ['admin-model-bindings'] });
  };

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => modelsAdminApi.toggle(model.model_id, enabled),
    onSuccess: invalidate,
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });
  const remove = useMutation({
    mutationFn: () => modelsAdminApi.remove(model.model_id),
    onSuccess: () => {
      invalidate();
      toast.success('已删除模型');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });
  const usedBindings = bindings.filter((binding) => binding.model_id === model.model_id);

  return (
    <tr className="hover:bg-gray-50/70">
      <td className="px-5 py-3.5">
        <div className="flex min-w-0 items-start gap-3">
          <div className={cn('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border', modelTypeTone(model.model_type))}>
            {model.model_type === 'chat' ? <Brain size={15} /> : model.model_type === 'embedding' ? <Database size={15} /> : <Layers size={15} />}
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium">{model.display_name || model.name}</div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-400">
              <span className="font-mono">{model.name}</span>
              <span>·</span>
              <span>{model.providerName}</span>
              {!model.providerEnabled && (
                <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">供应商已停用</span>
              )}
            </div>
          </div>
        </div>
      </td>
      <td className="px-4 py-3.5">
        <span className={cn('inline-flex rounded-full border px-2 py-0.5 text-xs', modelTypeTone(model.model_type))}>
          {modelTypeLabel(model.model_type)}
        </span>
      </td>
      <td className="px-4 py-3.5">
        <div className="flex max-w-52 flex-wrap gap-1.5">
          {usedBindings.length === 0 ? (
            <span className="text-xs text-gray-400">未绑定</span>
          ) : usedBindings.map((binding) => (
            <span key={binding.role} className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] text-orange-700">
              {roleLabel(binding.role)}
            </span>
          ))}
        </div>
      </td>
      <td className="px-4 py-3.5 text-xs text-gray-600">
        {configSummary(model)}
      </td>
      <td className="px-4 py-3.5">
        <div className="flex items-center gap-2">
          <Switch
            checked={model.is_enabled}
            loading={toggle.isPending}
            onChange={(value) => toggle.mutate(value)}
            size="sm"
          />
          <span className={cn('text-xs', model.is_enabled ? 'text-green-600' : 'text-gray-400')}>
            {model.is_enabled ? '启用' : '停用'}
          </span>
        </div>
      </td>
      <td className="px-4 py-3.5">
        <div className="flex justify-end gap-1.5">
        <button
          onClick={onEdit}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          title="编辑"
        >
          <Edit2 size={14} />
        </button>
        <button
          onClick={async () => {
            if (await confirm({ message: `确定删除模型 ${model.name}?`, confirmLabel: '删除', danger: true }))
              remove.mutate();
          }}
          className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
          title="删除"
        >
          <Trash2 size={14} />
        </button>
        </div>
      </td>
    </tr>
  );
}

function configSummary(model: ProviderModel) {
  const config = model.config || {};
  if (model.model_type === 'chat') {
    const caps = (config.capabilities as string[] | undefined) || [];
    return (
      <div className="space-y-1">
        <div>上下文 {fmtNumber(config.context_window)} · 输出 {fmtNumber(config.max_output_tokens)}</div>
        <div className="text-gray-400">{caps.length > 0 ? caps.join(', ') : '无特殊能力'}</div>
      </div>
    );
  }
  if (model.model_type === 'embedding') {
    return (
      <div className="space-y-1">
        <div>维度 {fmtNumber(config.dimension)} · 批大小 {fmtNumber(config.batch_size)}</div>
        <div className="text-gray-400">最大批 {fmtNumber(config.max_batch_size)}</div>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div>超时 {fmtNumber(config.timeout_seconds)}s · 最大字符 {fmtNumber(config.max_doc_chars)}</div>
      <div className="text-gray-400">截断 {String(config.truncation_strategy || 'tail')}</div>
    </div>
  );
}

function ProviderAccessPanel({
  providers,
  onCreateModel,
}: {
  providers: ProviderAdmin[];
  onCreateModel: (providerName: string) => void;
}) {
  return (
    <section className="rounded-xl border bg-white shadow-sm">
      <div className="border-b px-5 py-4">
        <h2 className="text-sm font-semibold text-gray-900">供应商访问配置</h2>
        <p className="mt-0.5 text-xs text-gray-500">供应商启停、API-Key 权重和冷却状态仍按供应商管理。</p>
      </div>
      {providers.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-400">
          <Server className="mx-auto mb-2 text-gray-300" size={34} />
          还没有供应商
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 p-5 xl:grid-cols-2">
          {providers.map((provider) => (
            <ProviderAccessCard key={provider.id} provider={provider} onCreateModel={onCreateModel} />
          ))}
        </div>
      )}
    </section>
  );
}

function ProviderAccessCard({
  provider,
  onCreateModel,
}: {
  provider: ProviderAdmin;
  onCreateModel: (providerName: string) => void;
}) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => providersApi.toggle(provider.name, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
      toast.success(provider.is_enabled ? '已禁用供应商' : '已启用供应商');
    },
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });

  return (
    <div className="rounded-lg border bg-gray-50/40">
      <div className="flex items-start justify-between gap-3 border-b bg-white px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-600">
            <Server size={17} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium">{provider.name}</span>
              {provider.impl && provider.impl !== provider.name && (
                <span className="text-xs text-gray-400">{provider.impl}</span>
              )}
            </div>
            <div className="mt-0.5 truncate text-xs text-gray-400">
              {provider.base_url || '未配置 base_url'}
            </div>
          </div>
        </div>
        <Switch checked={provider.is_enabled} loading={toggle.isPending} onChange={(value) => toggle.mutate(value)} />
      </div>
      <div className="grid grid-cols-3 gap-2 px-4 py-3 text-xs">
        <div className="rounded-md bg-white px-3 py-2">
          <div className="text-gray-400">模型</div>
          <div className="mt-1 font-semibold">{provider.model_count}</div>
        </div>
        <div className="rounded-md bg-white px-3 py-2">
          <div className="text-gray-400">API-Key</div>
          <div className="mt-1 font-semibold">{provider.key_count}</div>
        </div>
        <button
          onClick={() => onCreateModel(provider.name)}
          className="rounded-md border border-orange-100 bg-orange-50 px-3 py-2 text-left text-orange-700 hover:bg-orange-100"
        >
          <Plus size={13} className="mb-1" />
          新增模型
        </button>
      </div>
      <KeysSection providerName={provider.name} />
    </div>
  );
}

// ============================================================================
// API-Key 区
// ============================================================================
function KeysSection({ providerName }: { providerName: string }) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const keysKey = ['provider-keys', providerName];

  const { data: keys, isLoading } = useQuery({
    queryKey: keysKey,
    queryFn: () => providerKeysApi.list(providerName),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: keysKey });
    qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
  };

  return (
    <div className="border-t bg-gray-50/50 px-5 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1 text-xs font-medium text-gray-500">
          <KeyRound size={12} /> API-Key
        </span>
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 text-xs text-orange-600 hover:text-orange-700"
        >
          <Plus size={13} /> 添加 Key
        </button>
      </div>

      {isLoading ? (
        <div className="py-2 text-center text-xs text-gray-400">加载中…</div>
      ) : !keys || keys.length === 0 ? (
        <div className="py-2 text-center text-xs text-gray-400">暂无 API-Key</div>
      ) : (
        <div className="space-y-1.5">
          {keys.map((k) => (
            <KeyRow key={k.key_id} providerName={providerName} k={k} onChanged={invalidate} />
          ))}
        </div>
      )}

      {adding && (
        <AddKeyDialog
          providerName={providerName}
          onClose={() => setAdding(false)}
          onCreated={invalidate}
        />
      )}
    </div>
  );
}

function KeyRow({
  providerName,
  k,
  onChanged,
}: {
  providerName: string;
  k: ProviderKey;
  onChanged: () => void;
}) {
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      providerKeysApi.update(providerName, k.key_id, { enabled }),
    onSuccess: onChanged,
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });
  const remove = useMutation({
    mutationFn: () => providerKeysApi.remove(providerName, k.key_id),
    onSuccess: () => {
      onChanged();
      toast.success('已删除 Key');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });

  const cooling = k.cooldown_until && new Date(k.cooldown_until).getTime() > Date.now();

  return (
    <div className="flex items-center justify-between rounded-md border border-gray-200 bg-white px-3 py-1.5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-xs text-gray-600">{k.key_fingerprint}</span>
        <span className="text-[10px] text-gray-400">权重 {k.weight}</span>
        {cooling && (
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-500">冷却中</span>
        )}
        {k.failure_score > 0 && (
          <span className="text-[10px] text-gray-400">失败分 {k.failure_score}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          onClick={async () => {
            if (await confirm({ message: `确定删除 Key ${k.key_fingerprint}?`, confirmLabel: '删除', danger: true }))
              remove.mutate();
          }}
          className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
          title="删除"
        >
          <Trash2 size={13} />
        </button>
        <Switch
          checked={k.is_enabled}
          loading={toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
          size="sm"
        />
      </div>
    </div>
  );
}

// ============================================================================
// 模型新增 / 编辑弹窗
// ============================================================================
function ModelDialog({
  providerName,
  model,
  onClose,
}: {
  providerName: string;
  model: ProviderModel | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const editing = !!model;
  const modelDbId = model?.id ?? '';
  const [name, setName] = useState(model?.name || '');
  const [displayName, setDisplayName] = useState(model?.display_name || '');
  const [modelType, setModelType] = useState<ProviderModel['model_type']>(model?.model_type || 'chat');
  const initialConfig = model?.config || {};
  const [contextWindow, setContextWindow] = useState(Number(initialConfig.context_window ?? 128000));
  const [maxOutputTokens, setMaxOutputTokens] = useState(Number(initialConfig.max_output_tokens ?? 4096));
  const [dimension, setDimension] = useState(Number(initialConfig.dimension ?? 1024));
  const [batchSize, setBatchSize] = useState(Number(initialConfig.batch_size ?? 10));
  const [supportedDimensionsText, setSupportedDimensionsText] = useState(
    ((initialConfig.supported_dimensions as number[] | undefined) ?? [Number(initialConfig.dimension ?? 1024)]).join(', '),
  );
  const [maxBatchSize, setMaxBatchSize] = useState(Number(initialConfig.max_batch_size ?? initialConfig.batch_size ?? 10));
  const [timeoutSeconds, setTimeoutSeconds] = useState(Number(initialConfig.timeout_seconds ?? 5));
  const [maxRetries, setMaxRetries] = useState(Number(initialConfig.max_retries ?? 3));
  const [retryBackoff, setRetryBackoff] = useState(Number(initialConfig.retry_backoff ?? 1));
  const [truncationStrategy, setTruncationStrategy] = useState(String(initialConfig.truncation_strategy ?? 'tail'));
  const [maxDocChars, setMaxDocChars] = useState(Number(initialConfig.max_doc_chars ?? 4000));
  const [monitorThreshold, setMonitorThreshold] = useState(Number(initialConfig.monitor_threshold ?? 0.1));
  const initialCaps = (initialConfig.capabilities as string[] | undefined) || [];
  const [supportsTools, setSupportsTools] = useState(initialCaps.includes('tools'));
  const [supportsImages, setSupportsImages] = useState(initialCaps.includes('vision'));
  const [supportsThinking, setSupportsThinking] = useState(initialCaps.includes('thinking'));
  const [thinkingOptions, setThinkingOptions] = useState<ThinkingLevel[]>(
    ((initialConfig.thinking_options as string[] | undefined) || []).filter(isThinkingLevel),
  );
  const [providerOptionsText, setProviderOptionsText] = useState(
    JSON.stringify(initialConfig.provider_options ?? {}, null, 2),
  );

  const { data: latestModel, isLoading: loadingDetail } = useQuery({
    queryKey: ['admin-model-detail', modelDbId],
    queryFn: () => modelsAdminApi.detail(modelDbId),
    enabled: editing && !!modelDbId,
  });

  useEffect(() => {
    if (!latestModel) return;
    setName(latestModel.name || '');
    setDisplayName(latestModel.display_name || '');
    setModelType(latestModel.model_type || 'chat');
    const config = latestModel.config || {};
    setContextWindow(Number(config.context_window ?? 128000));
    setMaxOutputTokens(Number(config.max_output_tokens ?? 4096));
    setDimension(Number(config.dimension ?? 1024));
    setBatchSize(Number(config.batch_size ?? 10));
    setSupportedDimensionsText(
      ((config.supported_dimensions as number[] | undefined) ?? [Number(config.dimension ?? 1024)]).join(', '),
    );
    setMaxBatchSize(Number(config.max_batch_size ?? config.batch_size ?? 10));
    setTimeoutSeconds(Number(config.timeout_seconds ?? 5));
    setMaxRetries(Number(config.max_retries ?? 3));
    setRetryBackoff(Number(config.retry_backoff ?? 1));
    setTruncationStrategy(String(config.truncation_strategy ?? 'tail'));
    setMaxDocChars(Number(config.max_doc_chars ?? 4000));
    setMonitorThreshold(Number(config.monitor_threshold ?? 0.1));
    const caps = (config.capabilities as string[] | undefined) || [];
    setSupportsTools(caps.includes('tools'));
    setSupportsImages(caps.includes('vision'));
    setSupportsThinking(caps.includes('thinking'));
    setThinkingOptions(((config.thinking_options as string[] | undefined) || []).filter(isThinkingLevel));
    setProviderOptionsText(JSON.stringify(config.provider_options ?? {}, null, 2));
  }, [latestModel]);

  const toggleThinkingOption = (level: ThinkingLevel) => {
    setThinkingOptions((prev) => {
      const next = prev.includes(level)
        ? prev.filter((x) => x !== level)
        : [...prev, level];
      return THINKING_LEVELS.filter((x) => next.includes(x));
    });
  };

  const save = useMutation({
    mutationFn: () => {
      let providerOptions: Record<string, unknown> = {};
      const raw = providerOptionsText.trim();
      if (raw.length > 0) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          throw new Error('provider_options 不是合法 JSON');
        }
        if (parsed === null) {
          providerOptions = {};
        } else if (typeof parsed === 'object' && !Array.isArray(parsed)) {
          providerOptions = parsed as Record<string, unknown>;
        } else {
          throw new Error('provider_options 必须是 JSON 对象');
        }
      }

      const enabledThinkingOptions = THINKING_LEVELS.filter((l) => thinkingOptions.includes(l));
      const supportedDimensions = supportedDimensionsText
        .split(',')
        .map((value) => Number(value.trim()));
      if (
        modelType === 'embedding'
        && (
          supportedDimensions.length === 0
          || supportedDimensions.some((value) => !Number.isInteger(value) || value <= 0)
        )
      ) {
        throw new Error('支持的向量维度必须是逗号分隔的正整数');
      }

      const capabilities = [
        ...(supportsTools ? ['tools'] : []),
        ...(supportsImages ? ['vision'] : []),
        ...(supportsThinking ? ['thinking'] : []),
      ];
      const config: Record<string, unknown> = modelType === 'chat'
        ? {
            context_window: contextWindow,
            max_output_tokens: maxOutputTokens,
            input_modalities: supportsImages ? ['text', 'image'] : ['text'],
            output_modalities: ['text'],
            capabilities,
            thinking_options: supportsThinking
              ? (enabledThinkingOptions.length > 0 ? enabledThinkingOptions : null)
              : null,
            provider_options: providerOptions,
          }
        : modelType === 'embedding'
          ? {
              dimension,
              batch_size: batchSize,
              supported_dimensions: supportedDimensions,
              max_batch_size: maxBatchSize,
              input_modalities: ['text'],
              max_retries: maxRetries,
              retry_backoff: retryBackoff,
              provider_options: providerOptions,
            }
          : {
              timeout_seconds: timeoutSeconds,
              max_retries: maxRetries,
              retry_backoff: retryBackoff,
              truncation_strategy: truncationStrategy,
              max_doc_chars: maxDocChars,
              monitor_threshold: monitorThreshold,
              provider_options: providerOptions,
            };
      const payload: ModelUpsert = {
        display_name: displayName.trim(),
        config,
      };
      return editing
        ? modelsAdminApi.update(model.model_id, payload)
        : modelsAdminApi.create(providerName, {
            ...payload,
            name: name.trim(),
            model_type: modelType,
          });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
      toast.success(editing ? '已保存' : '已新增模型');
      onClose();
    },
    onError: (e) => toast.error((e as ApiError).message || (e as Error).message || '保存失败'),
  });

  const canSubmit = name.trim().length > 0;

  return (
    <Modal title={editing ? '编辑模型' : '新增模型'} onClose={onClose}>
      <div className="space-y-3">
        {editing && loadingDetail && (
          <div className="rounded-md border border-orange-100 bg-orange-50 px-3 py-2 text-xs text-orange-700">
            正在从后台加载最新模型配置...
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="模型名 *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="gpt-4o"
              disabled={editing}
              className={cn(inputCls, 'font-mono')}
            />
          </Field>
          <Field label="显示名">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="GPT-4o"
              className={inputCls}
            />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select
              value={modelType}
              disabled={editing}
              onChange={(e) => setModelType(e.target.value as ProviderModel['model_type'])}
              className={cn(inputCls, 'bg-white')}
            >
              <option value="chat">chat</option>
              <option value="embedding">embedding</option>
              <option value="reranker">reranker</option>
            </select>
          </Field>
          <Field label="上下文窗口">
                <input type="number" value={contextWindow} onChange={(e) => setContextWindow(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
        </div>
        {modelType === 'chat' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="最大输出 Token">
                <input type="number" value={maxOutputTokens} onChange={(e) => setMaxOutputTokens(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
            </div>
            <Field label="能力">
              <div className="flex flex-wrap gap-2">
                <CapToggle label="工具调用" on={supportsTools} onClick={() => setSupportsTools((v) => !v)} />
                <CapToggle label="图片识别" on={supportsImages} onClick={() => setSupportsImages((v) => !v)} />
                <CapToggle label="思考模式" on={supportsThinking} onClick={() => setSupportsThinking((v) => !v)} />
              </div>
            </Field>
            <Field label="thinking_options">
              <div className="flex flex-wrap gap-2">
                {THINKING_LEVELS.map((level) => (
                  <CapToggle key={level} label={level} on={thinkingOptions.includes(level)} onClick={() => toggleThinkingOption(level)} />
                ))}
              </div>
            </Field>
          </>
        )}
        {modelType === 'embedding' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="向量维度">
                <input type="number" value={dimension} disabled={editing} onChange={(e) => setDimension(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
              <Field label="批大小">
                <input type="number" value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="支持的向量维度">
                <input value={supportedDimensionsText} onChange={(e) => setSupportedDimensionsText(e.target.value)} placeholder="1024, 768, 512" className={inputCls} />
              </Field>
              <Field label="最大批大小">
                <input type="number" value={maxBatchSize} onChange={(e) => setMaxBatchSize(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
            </div>
          </>
        )}
        {modelType === 'reranker' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="超时秒数">
                <input type="number" value={timeoutSeconds} onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
              <Field label="截断策略">
                <select value={truncationStrategy} onChange={(e) => setTruncationStrategy(e.target.value)} className={cn(inputCls, 'bg-white')}>
                  <option value="tail">tail</option>
                </select>
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="单文档最大字符数">
                <input type="number" value={maxDocChars} onChange={(e) => setMaxDocChars(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
              <Field label="截断监控阈值">
                <input type="number" step="0.01" value={monitorThreshold} onChange={(e) => setMonitorThreshold(Number(e.target.value) || 0)} className={inputCls} />
              </Field>
            </div>
          </>
        )}
        {modelType !== 'chat' && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="最大重试次数">
              <input type="number" value={maxRetries} onChange={(e) => setMaxRetries(Number(e.target.value) || 0)} className={inputCls} />
            </Field>
            <Field label="重试退避秒数">
              <input type="number" value={retryBackoff} onChange={(e) => setRetryBackoff(Number(e.target.value) || 0)} className={inputCls} />
            </Field>
          </div>
        )}
        <Field label="provider_options (JSON)">
          <textarea
            value={providerOptionsText}
            onChange={(e) => setProviderOptionsText(e.target.value)}
            rows={6}
            placeholder="{}"
            className={cn(inputCls, 'font-mono')}
          />
        </Field>
      </div>
      <DialogActions
        onClose={onClose}
        onSave={() => save.mutate()}
        saving={save.isPending}
        disabled={!canSubmit || (editing && loadingDetail)}
      />
    </Modal>
  );
}

// ============================================================================
// 添加 Key 弹窗
// ============================================================================
function AddKeyDialog({
  providerName,
  onClose,
  onCreated,
}: {
  providerName: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [apiKey, setApiKey] = useState('');
  const [weight, setWeight] = useState(1);

  const save = useMutation({
    mutationFn: () => providerKeysApi.create(providerName, apiKey.trim(), weight),
    onSuccess: () => {
      onCreated();
      toast.success('已添加 Key');
      onClose();
    },
    onError: (e) => toast.error((e as ApiError).message || '添加失败'),
  });

  return (
    <Modal title="添加 API-Key" onClose={onClose}>
      <div className="space-y-3">
        <Field label="API Key（明文，提交后加密存储）">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
            className={cn(inputCls, 'font-mono')}
            autoFocus
          />
        </Field>
        <Field label="负载权重">
          <input
            type="number"
            min={1}
            value={weight}
            onChange={(e) => setWeight(Math.max(1, Number(e.target.value) || 1))}
            className={inputCls}
          />
        </Field>
      </div>
      <DialogActions
        onClose={onClose}
        onSave={() => save.mutate()}
        saving={save.isPending}
        disabled={apiKey.trim().length === 0}
      />
    </Modal>
  );
}

// ============================================================================
// 通用小组件
// ============================================================================
function Switch({
  checked,
  onChange,
  loading,
  size = 'md',
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  loading?: boolean;
  size?: 'sm' | 'md';
}) {
  const w = size === 'sm' ? 'h-4 w-7' : 'h-5 w-9';
  const dot = size === 'sm' ? 'h-3 w-3' : 'h-4 w-4';
  const translate = size === 'sm' ? 'translate-x-3' : 'translate-x-4';
  return (
    <button
      onClick={() => !loading && onChange(!checked)}
      disabled={loading}
      className={cn(
        'relative inline-flex shrink-0 items-center rounded-full transition-colors',
        w,
        checked ? 'bg-orange-500' : 'bg-gray-300',
        loading && 'opacity-60',
      )}
      title={checked ? '已启用' : '已禁用'}
    >
      <span
        className={cn(
          'absolute left-0.5 inline-flex items-center justify-center rounded-full bg-white transition-transform',
          dot,
          checked && translate,
        )}
      >
        {loading && <Loader2 size={size === 'sm' ? 8 : 10} className="animate-spin text-gray-400" />}
      </span>
    </button>
  );
}

function CapToggle({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded-md border px-2.5 py-1 text-xs',
        on ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 hover:bg-gray-50',
      )}
    >
      {label}
    </button>
  );
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div onClick={onClose} className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 className="mb-4 text-base font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}

function DialogActions({
  onClose,
  onSave,
  saving,
  disabled,
}: {
  onClose: () => void;
  onSave: () => void;
  saving: boolean;
  disabled: boolean;
}) {
  return (
    <div className="mt-5 flex justify-end gap-2">
      <button onClick={onClose} className="rounded-md border px-3 py-1.5 text-sm hover:bg-gray-50">
        取消
      </button>
      <button
        onClick={onSave}
        disabled={disabled || saving}
        className="rounded-md bg-orange-500 px-3 py-1.5 text-sm text-white hover:bg-orange-600 disabled:opacity-50"
      >
        {saving ? '保存中…' : '保存'}
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-gray-600">{label}</label>
      {children}
    </div>
  );
}
