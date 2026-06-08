import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChevronDown, ChevronUp, Link2, Plus, RefreshCw, Save, ShieldCheck, Trash2,
} from 'lucide-react';

import { modelBindingsApi, modelChainsApi, providersApi, ragIndexAdminApi } from '@/api';
import {
  ApiError, ModelChain, ModelChainEntry, ModelChainScope, ProviderAdmin, RagIndexStatus,
  SystemModelBinding,
} from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';
import {
  PROVIDERS_KEY, ROLE_ORDER, inputCls, roleDescription, roleIcon, roleLabel,
} from '@/components/admin/modelShared';

const TIER_KEYS = ['fast', 'smart', 'strong'] as const;
const TIER_META: Record<(typeof TIER_KEYS)[number], { label: string; desc: string }> = {
  fast: { label: '快速档 fast', desc: '低成本低延迟,用于摘要 / 标题等 utility 与轻量对话' },
  smart: { label: '均衡档 smart', desc: '默认对话档位,平衡质量与成本' },
  strong: { label: '强力档 strong', desc: '复杂推理 / 规划等高质量任务' },
};

type ChatModelOption = { provider: string; model: string; label: string };

export default function AdminModelsPage() {
  const { data: providers } = useQuery({
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

  return (
    <div className="h-full overflow-y-auto bg-gray-50/40">
      <div className="mx-auto max-w-7xl px-8 py-8">
        <div className="mb-6">
          <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-orange-50 px-2.5 py-1 text-xs text-orange-700">
            <ShieldCheck size={13} />
            系统模型运行时配置
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">模型配置</h1>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            配置 RAG / 语义历史的系统绑定模型,以及对话与各档位的模型调用链(fallback 顺序)。
            供应商接入与模型清单请前往「供应商与模型」页面。
          </p>
        </div>

        <SystemBindings providers={providerList} bindings={bindings || []} ragStatus={ragStatus} />

        <ChainConfig providers={providerList} />
      </div>
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
    .flatMap((provider) => provider.models.map((model) => ({ ...model, providerName: provider.name })));
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
          const current = binding ? models.find((m) => m.model_id === binding.model_id) : undefined;
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
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500">v{binding.version}</span>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{roleDescription(role)}</p>
                </div>
              </div>
              <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">当前绑定</div>
                <div className="mt-1 truncate text-sm font-medium">
                  {current ? (
                    <>{current.providerName} / {current.display_name || current.name}</>
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
                          <button onClick={() => retry.mutate(latestJob.id)} disabled={retry.isPending} className="text-orange-600 disabled:text-gray-300">
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

function IndexPill({ label, value, warn, danger }: { label: string; value: number; warn?: boolean; danger?: boolean }) {
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

// ============================================================================
// 模型调用链配置(档位链 / 对话链)
// ============================================================================
function ChainConfig({ providers }: { providers: ProviderAdmin[] }) {
  const enabledProviders = providers.filter((p) => p.is_enabled);
  const allChatModels: ChatModelOption[] = enabledProviders.flatMap((p) =>
    p.models
      .filter((m) => m.model_type === 'chat' && m.is_enabled)
      .map((m) => ({ provider: p.name, model: m.name, label: m.display_name || m.name })),
  );
  const labelOf = (provider: string, model: string) =>
    allChatModels.find((x) => x.provider === provider && x.model === model)?.label || model;

  const { data: tierChains } = useQuery({
    queryKey: ['admin-model-chains', 'tier'],
    queryFn: () => modelChainsApi.list('tier'),
  });
  const { data: convChains } = useQuery({
    queryKey: ['admin-model-chains', 'conversation'],
    queryFn: () => modelChainsApi.list('conversation'),
  });

  const findChain = (chains: ModelChain[] | undefined, key: string) =>
    chains?.find((c) => c.chain_key === key);

  return (
    <section className="space-y-5">
      <div className="flex items-center gap-2">
        <Link2 size={16} className="text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900">模型调用链</h2>
      </div>

      {/* 档位链 */}
      <div className="rounded-xl border bg-white p-5 shadow-sm">
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-gray-900">档位链</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            CLI 任务与 utility 按档位选模型,<b>可跨供应商</b>;主模型失败时按链顺序降级。
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          {TIER_KEYS.map((tier) => (
            <ChainEditor
              key={tier}
              scope="tier"
              chainKey={tier}
              title={TIER_META[tier].label}
              subtitle={TIER_META[tier].desc}
              chain={findChain(tierChains, tier)}
              available={allChatModels}
              labelOf={labelOf}
            />
          ))}
        </div>
      </div>

      {/* 对话链 */}
      <div className="rounded-xl border bg-white p-5 shadow-sm">
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-gray-900">对话链</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Web 对话用户指定模型后的<b>同供应商</b>后备顺序;每个供应商单独配置,绝不跨厂商。
          </p>
        </div>
        {enabledProviders.length === 0 ? (
          <div className="py-8 text-center text-sm text-gray-400">暂无启用的供应商</div>
        ) : (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {enabledProviders.map((p) => (
              <ChainEditor
                key={p.name}
                scope="conversation"
                chainKey={p.name}
                title={p.name}
                subtitle={`${p.name} 的同供应商后备顺序`}
                chain={findChain(convChains, p.name)}
                available={allChatModels.filter((m) => m.provider === p.name)}
                labelOf={labelOf}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

const EMPTY_ENTRIES: ModelChainEntry[] = [];

function ChainEditor({
  scope,
  chainKey,
  title,
  subtitle,
  chain,
  available,
  labelOf,
}: {
  scope: ModelChainScope;
  chainKey: string;
  title: string;
  subtitle: string;
  chain: ModelChain | undefined;
  available: ChatModelOption[];
  labelOf: (provider: string, model: string) => string;
}) {
  const qc = useQueryClient();
  const remoteEntries = chain?.entries ?? EMPTY_ENTRIES;
  const version = chain?.version ?? -1;

  const [local, setLocal] = useState<ModelChainEntry[]>(remoteEntries);
  const [syncedVersion, setSyncedVersion] = useState(version);
  const [addSel, setAddSel] = useState('');
  // 远端版本变化(保存后 refetch)→ 重置本地编辑态
  if (version !== syncedVersion) {
    setSyncedVersion(version);
    setLocal(remoteEntries);
  }

  const dirty = JSON.stringify(local) !== JSON.stringify(remoteEntries);
  const addable = available.filter((a) => !local.some((e) => e.provider === a.provider && e.model === a.model));

  const save = useMutation({
    mutationFn: () => modelChainsApi.update(scope, chainKey, local),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-model-chains', scope] });
      toast.success('调用链已保存');
    },
    onError: (e) => toast.error((e as ApiError).message || '保存失败'),
  });

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= local.length) return;
    const next = [...local];
    [next[i], next[j]] = [next[j], next[i]];
    setLocal(next);
  };
  const removeAt = (i: number) => setLocal(local.filter((_, j) => j !== i));
  const add = () => {
    if (!addSel) return;
    // value 形如 "provider model";provider 名不含空格,按首个空格切分
    const sep = addSel.indexOf(' ');
    const provider = sep < 0 ? '' : addSel.slice(0, sep);
    const model = sep < 0 ? '' : addSel.slice(sep + 1);
    if (!provider || !model) return;
    if (local.some((e) => e.provider === provider && e.model === model)) return;
    setLocal([...local, { provider, model }]);
    setAddSel('');
  };

  return (
    <div className="rounded-lg border bg-gray-50/40 p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold">{title}</div>
          <div className="mt-0.5 line-clamp-1 text-[11px] text-gray-400">{subtitle}</div>
        </div>
        <button
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-orange-500 px-2.5 py-1 text-xs text-white hover:bg-orange-600 disabled:opacity-40"
        >
          <Save size={12} /> {save.isPending ? '保存中' : '保存'}
        </button>
      </div>

      {local.length === 0 ? (
        <div className="rounded-md border border-dashed bg-white py-4 text-center text-xs text-gray-400">
          未配置;运行时回退到系统默认模型
        </div>
      ) : (
        <ol className="space-y-1.5">
          {local.map((e, i) => (
            <li key={`${e.provider}:${e.model}`} className="flex items-center gap-2 rounded-md border bg-white px-2.5 py-1.5">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-gray-100 text-[11px] font-medium text-gray-500">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{labelOf(e.provider, e.model)}</div>
                <div className="truncate font-mono text-[10px] text-gray-400">{e.provider} / {e.model}</div>
              </div>
              <div className="flex shrink-0 items-center gap-0.5">
                <button onClick={() => move(i, -1)} disabled={i === 0} className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-30" title="上移">
                  <ChevronUp size={13} />
                </button>
                <button onClick={() => move(i, 1)} disabled={i === local.length - 1} className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-30" title="下移">
                  <ChevronDown size={13} />
                </button>
                <button onClick={() => removeAt(i)} className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600" title="移除">
                  <Trash2 size={13} />
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="mt-2 flex items-center gap-2">
        <select
          value={addSel}
          onChange={(e) => setAddSel(e.target.value)}
          disabled={addable.length === 0}
          className={cn(inputCls, 'h-8 bg-white py-1 text-xs')}
        >
          <option value="">{addable.length === 0 ? '无可添加模型' : '选择模型加入链…'}</option>
          {addable.map((a) => (
            <option key={`${a.provider}:${a.model}`} value={`${a.provider} ${a.model}`}>
              {scope === 'tier' ? `${a.provider} / ${a.label}` : a.label}
            </option>
          ))}
        </select>
        <button
          onClick={add}
          disabled={!addSel}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-white disabled:opacity-40"
        >
          <Plus size={12} /> 添加
        </button>
      </div>
    </div>
  );
}
