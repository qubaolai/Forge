import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Brain, Database, Edit2, KeyRound, Layers, Plus, Search, Server, Trash2,
} from 'lucide-react';

import { modelChainsApi, modelsAdminApi, providerKeysApi, providersApi } from '@/api';
import {
  ApiError, ModelChain, ModelUpsert, ProviderAdmin, ProviderKey, ProviderModel,
} from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import {
  AdminModelItem, CapToggle, DialogActions, Field, Modal, ModelStatusFilter, ModelTypeFilter,
  PROVIDERS_KEY, Switch, THINKING_LEVELS, ThinkingLevel, fmtNumber, inputCls, isThinkingLevel,
  modelTypeLabel, modelTypeTone,
} from '@/components/admin/modelShared';

/**
 * 调用链影响查询:返回引用了指定模型 / 供应商的链标签列表。
 * 用于在模型/供应商启停、删除、修改前提示「本次操作会影响 fallback 链」。
 */
function useChainImpact() {
  const { data: tier } = useQuery({
    queryKey: ['admin-model-chains', 'tier'],
    queryFn: () => modelChainsApi.list('tier'),
  });
  const { data: conv } = useQuery({
    queryKey: ['admin-model-chains', 'conversation'],
    queryFn: () => modelChainsApi.list('conversation'),
  });
  const all: ModelChain[] = [...(tier ?? []), ...(conv ?? [])];
  const label = (c: ModelChain) =>
    c.scope === 'tier' ? `档位链 ${c.chain_key}` : `对话链 ${c.chain_key}`;
  return {
    forModel: (provider: string, model: string) =>
      all.filter((c) => c.entries.some((e) => e.provider === provider && e.model === model)).map(label),
    forProvider: (provider: string) =>
      all.filter((c) => c.entries.some((e) => e.provider === provider)).map(label),
  };
}

function impactClause(affected: string[]) {
  return affected.length > 0
    ? `当前被以下调用链引用:${affected.join('、')}。`
    : '当前未被任何调用链引用。';
}

export default function AdminProvidersPage() {
  const [typeFilter, setTypeFilter] = useState<ModelTypeFilter>('all');
  const [providerFilter, setProviderFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<ModelStatusFilter>('all');
  const [createProviderSelection, setCreateProviderSelection] = useState('');
  const [creatingProviderName, setCreatingProviderName] = useState('');
  const [editingModel, setEditingModel] = useState<AdminModelItem | null>(null);

  const { data: providers, isLoading } = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: () => providersApi.listAdmin(),
  });
  const providerList = providers || [];
  const createProviderName =
    createProviderSelection || providerList.find((p) => p.is_enabled)?.name || providerList[0]?.name || '';
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
              <Server size={13} />
              供应商接入与模型注册
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">供应商与模型</h1>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              管理供应商访问(启停、base_url、API-Key)与模型清单(Chat / Embedding / Reranker)。
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

        <ModelInventory
          models={filteredModels}
          providers={providerList}
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
            providerName={editingModel ? editingModel.providerName : creatingProviderName}
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

function ModelInventory({
  models,
  providers,
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
  isLoading: boolean;
  typeFilter: ModelTypeFilter;
  providerFilter: string;
  statusFilter: ModelStatusFilter;
  onTypeFilterChange: (value: ModelTypeFilter) => void;
  onProviderFilterChange: (value: string) => void;
  onStatusFilterChange: (value: ModelStatusFilter) => void;
  onEdit: (model: AdminModelItem) => void;
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
              <th className="px-4 py-3 text-left font-medium">关键配置</th>
              <th className="px-4 py-3 text-left font-medium">状态</th>
              <th className="w-32 px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr>
                <td colSpan={5} className="py-14 text-center text-sm text-gray-400">加载中…</td>
              </tr>
            )}
            {!isLoading && models.length === 0 && (
              <tr>
                <td colSpan={5} className="py-14 text-center text-sm text-gray-400">没有符合筛选条件的模型</td>
              </tr>
            )}
            {models.map((model) => (
              <ModelInventoryRow key={model.model_id} model={model} onEdit={() => onEdit(model)} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ModelInventoryRow({ model, onEdit }: { model: AdminModelItem; onEdit: () => void }) {
  const qc = useQueryClient();
  const impact = useChainImpact();
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
      <td className="px-4 py-3.5 text-xs text-gray-600">{configSummary(model)}</td>
      <td className="px-4 py-3.5">
        <div className="flex items-center gap-2">
          <Switch
            checked={model.is_enabled}
            loading={toggle.isPending}
            size="sm"
            onChange={async (value) => {
              const affected = impact.forModel(model.providerName, model.name);
              const action = value ? '启用' : '停用';
              const tail = value
                ? '启用后引用它的调用链将重新纳入该模型。'
                : '停用后引用它的调用链会在运行时跳过该模型(fallback 链变短)。';
              const ok = await confirm({
                title: `${action}模型`,
                message: `${action}模型「${model.display_name || model.name}」会影响模型调用链。${impactClause(affected)}${tail}是否继续?`,
                confirmLabel: action,
                danger: !value,
              });
              if (ok) toggle.mutate(value);
            }}
          />
          <span className={cn('text-xs', model.is_enabled ? 'text-green-600' : 'text-gray-400')}>
            {model.is_enabled ? '启用' : '停用'}
          </span>
        </div>
      </td>
      <td className="px-4 py-3.5">
        <div className="flex justify-end gap-1.5">
          <button onClick={onEdit} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700" title="编辑">
            <Edit2 size={14} />
          </button>
          <button
            onClick={async () => {
              const affected = impact.forModel(model.providerName, model.name);
              const ok = await confirm({
                title: '删除模型',
                message: `删除模型「${model.name}」不可恢复,且会影响模型调用链。${impactClause(affected)}删除后引用它的调用链会在运行时跳过该模型(fallback 链变短)。确定删除?`,
                confirmLabel: '删除',
                danger: true,
              });
              if (ok) remove.mutate();
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
  const impact = useChainImpact();
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
        <Switch
          checked={provider.is_enabled}
          loading={toggle.isPending}
          onChange={async (value) => {
            const affected = impact.forProvider(provider.name);
            const action = value ? '启用' : '禁用';
            const tail = value
              ? '启用后其模型将重新可用于调用链。'
              : '禁用后其全部模型在所有调用链中都会被跳过(fallback 链变短)。';
            const ok = await confirm({
              title: `${action}供应商`,
              message: `${action}供应商「${provider.name}」会影响模型调用链。${impactClause(affected)}${tail}是否继续?`,
              confirmLabel: action,
              danger: !value,
            });
            if (ok) toggle.mutate(value);
          }}
        />
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
        <AddKeyDialog providerName={providerName} onClose={() => setAdding(false)} onCreated={invalidate} />
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
    mutationFn: (enabled: boolean) => providerKeysApi.update(providerName, k.key_id, { enabled }),
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
        {cooling && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-500">冷却中</span>}
        {k.failure_score > 0 && <span className="text-[10px] text-gray-400">失败分 {k.failure_score}</span>}
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
        <Switch checked={k.is_enabled} loading={toggle.isPending} onChange={(v) => toggle.mutate(v)} size="sm" />
      </div>
    </div>
  );
}

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
  const impact = useChainImpact();
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
      const next = prev.includes(level) ? prev.filter((x) => x !== level) : [...prev, level];
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
      const supportedDimensions = supportedDimensionsText.split(',').map((value) => Number(value.trim()));
      if (
        modelType === 'embedding'
        && (supportedDimensions.length === 0 || supportedDimensions.some((value) => !Number.isInteger(value) || value <= 0))
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
      const payload: ModelUpsert = { display_name: displayName.trim(), config };
      return editing
        ? modelsAdminApi.update(model.model_id, payload)
        : modelsAdminApi.create(providerName, { ...payload, name: name.trim(), model_type: modelType });
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
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="gpt-4o" disabled={editing} className={cn(inputCls, 'font-mono')} />
          </Field>
          <Field label="显示名">
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="GPT-4o" className={inputCls} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select value={modelType} disabled={editing} onChange={(e) => setModelType(e.target.value as ProviderModel['model_type'])} className={cn(inputCls, 'bg-white')}>
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
          <textarea value={providerOptionsText} onChange={(e) => setProviderOptionsText(e.target.value)} rows={6} placeholder="{}" className={cn(inputCls, 'font-mono')} />
        </Field>
      </div>
      <DialogActions
        onClose={onClose}
        saving={save.isPending}
        disabled={!canSubmit || (editing && loadingDetail)}
        onSave={async () => {
          if (editing && model) {
            const affected = impact.forModel(providerName, model.name);
            const ok = await confirm({
              title: '保存模型修改',
              message: `修改模型「${model.name}」的配置可能改变它在调用链中的可用性(能力/上下文变化可能导致被过滤)。${impactClause(affected)}是否保存?`,
              confirmLabel: '保存',
            });
            if (!ok) return;
          }
          save.mutate();
        }}
      />
    </Modal>
  );
}

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
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." className={cn(inputCls, 'font-mono')} autoFocus />
        </Field>
        <Field label="负载权重">
          <input type="number" min={1} value={weight} onChange={(e) => setWeight(Math.max(1, Number(e.target.value) || 1))} className={inputCls} />
        </Field>
      </div>
      <DialogActions onClose={onClose} onSave={() => save.mutate()} saving={save.isPending} disabled={apiKey.trim().length === 0} />
    </Modal>
  );
}
