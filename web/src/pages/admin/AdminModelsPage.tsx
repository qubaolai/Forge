import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Plus, Edit2, Trash2, Star, KeyRound, Server, Loader2,
} from 'lucide-react';
import { providersApi, modelsAdminApi, providerKeysApi } from '@/api';
import { ApiError, ModelUpsert, ProviderAdmin, ProviderKey, ProviderModel } from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';

const PROVIDERS_KEY = ['admin-providers'];
const inputCls =
  'w-full rounded-md border px-3 py-1.5 text-sm outline-none focus:border-orange-400';
const THINKING_LEVELS = ['low', 'medium', 'high', 'xhigh', 'standard'] as const;
type ThinkingLevel = (typeof THINKING_LEVELS)[number];

function isThinkingLevel(value: string): value is ThinkingLevel {
  return THINKING_LEVELS.includes(value as ThinkingLevel);
}

export default function AdminModelsPage() {
  const { data: providers, isLoading } = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: () => providersApi.listAdmin(),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-8 py-8">
        <div className="mb-6">
          <h1 className="text-xl font-semibold">模型配置</h1>
          <p className="mt-1 text-sm text-gray-500">
            管理各供应商的启用状态、模型与 API-Key（不支持新增供应商）
          </p>
        </div>

        {isLoading ? (
          <div className="py-12 text-center text-sm text-gray-400">加载中…</div>
        ) : !providers || providers.length === 0 ? (
          <div className="rounded-lg border-2 border-dashed border-gray-200 py-16 text-center">
            <Server className="mx-auto text-gray-300" size={40} />
            <p className="mt-3 text-sm text-gray-500">还没有供应商</p>
          </div>
        ) : (
          <div className="space-y-4">
            {providers.map((p) => (
              <ProviderCard key={p.id} provider={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// 供应商卡片
// ============================================================================
function ProviderCard({ provider }: { provider: ProviderAdmin }) {
  const qc = useQueryClient();
  const [creatingModel, setCreatingModel] = useState(false);
  const [editingModel, setEditingModel] = useState<ProviderModel | null>(null);

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => providersApi.toggle(provider.name, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
      toast.success(provider.is_enabled ? '已禁用供应商' : '已启用供应商');
    },
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });

  return (
    <div className="rounded-lg border bg-white">
      {/* 供应商头部 */}
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-orange-50">
            <Server className="text-orange-500" size={18} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium">{provider.name}</span>
              {provider.impl && provider.impl !== provider.name && (
                <span className="text-xs text-gray-400">{provider.impl}</span>
              )}
            </div>
            <div className="text-xs text-gray-400">
              {provider.model_count} 个模型 · {provider.key_count} 个 Key
              {provider.base_url ? ` · ${provider.base_url}` : ''}
            </div>
          </div>
        </div>
        <Switch
          checked={provider.is_enabled}
          loading={toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
        />
      </div>

      {/* 模型区 */}
      <div className="px-5 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-gray-500">模型</span>
          <button
            onClick={() => setCreatingModel(true)}
            className="flex items-center gap-1 text-xs text-orange-600 hover:text-orange-700"
          >
            <Plus size={13} /> 新增模型
          </button>
        </div>
        {provider.models.length === 0 ? (
          <div className="py-3 text-center text-xs text-gray-400">暂无模型</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {provider.models.map((m) => (
              <ModelRow key={m.model_id} model={m} onEdit={() => setEditingModel(m)} />
            ))}
          </div>
        )}
      </div>

      {/* API-Key 区 */}
      <KeysSection providerName={provider.name} />

      {(creatingModel || editingModel) && (
        <ModelDialog
          providerName={provider.name}
          model={editingModel}
          onClose={() => {
            setCreatingModel(false);
            setEditingModel(null);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// 模型行
// ============================================================================
function ModelRow({ model, onEdit }: { model: ProviderModel; onEdit: () => void }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: PROVIDERS_KEY });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => modelsAdminApi.toggle(model.model_id, enabled),
    onSuccess: invalidate,
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });
  const setDefault = useMutation({
    mutationFn: () => modelsAdminApi.setDefault(model.model_id),
    onSuccess: () => {
      invalidate();
      toast.success('已设为默认');
    },
    onError: (e) => toast.error((e as ApiError).message || '操作失败'),
  });
  const remove = useMutation({
    mutationFn: () => modelsAdminApi.remove(model.model_id),
    onSuccess: () => {
      invalidate();
      toast.success('已删除');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });

  return (
    <div className="flex items-center justify-between py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm">{model.display_name || model.name}</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
          {model.model_type}
        </span>
        {model.is_default && (
          <span className="inline-flex items-center gap-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
            <Star size={9} className="fill-amber-500 text-amber-500" /> 默认
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {!model.is_default && model.is_enabled && (
          <button
            onClick={() => setDefault.mutate()}
            className="rounded p-1 text-gray-400 hover:bg-amber-50 hover:text-amber-600"
            title="设为默认"
          >
            <Star size={14} />
          </button>
        )}
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
        <Switch
          checked={model.is_enabled}
          loading={toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
          size="sm"
        />
      </div>
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
  const [modelType, setModelType] = useState(model?.model_type || 'text');
  const [contextWindow, setContextWindow] = useState(model?.context_window ?? 128000);
  const [costTier, setCostTier] = useState(model?.cost_tier || 'mid');
  const [supportsTools, setSupportsTools] = useState(model?.supports_tools ?? true);
  const [supportsImages, setSupportsImages] = useState(model?.supports_images ?? false);
  const [supportsThinking, setSupportsThinking] = useState(model?.supports_thinking ?? false);
  const [thinkingOptions, setThinkingOptions] = useState<ThinkingLevel[]>(
    (model?.thinking_options || []).filter(isThinkingLevel),
  );
  const [extraParamsText, setExtraParamsText] = useState(
    JSON.stringify(model?.extra_params ?? {}, null, 2),
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
    setModelType(latestModel.model_type || 'text');
    setContextWindow(latestModel.context_window ?? 128000);
    setCostTier(latestModel.cost_tier || 'mid');
    setSupportsTools(latestModel.supports_tools ?? true);
    setSupportsImages(latestModel.supports_images ?? false);
    setSupportsThinking(latestModel.supports_thinking ?? false);
    setThinkingOptions((latestModel.thinking_options || []).filter(isThinkingLevel));
    setExtraParamsText(JSON.stringify(latestModel.extra_params ?? {}, null, 2));
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
      let extraParams: Record<string, unknown> | null = null;
      const raw = extraParamsText.trim();
      if (raw.length > 0) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          throw new Error('extra_params 不是合法 JSON');
        }
        if (parsed === null) {
          extraParams = null;
        } else if (typeof parsed === 'object' && !Array.isArray(parsed)) {
          extraParams = parsed as Record<string, unknown>;
        } else {
          throw new Error('extra_params 必须是 JSON 对象');
        }
      }

      const enabledThinkingOptions = THINKING_LEVELS.filter((l) => thinkingOptions.includes(l));

      const payload: ModelUpsert = {
        name: name.trim(),
        display_name: displayName.trim(),
        model_type: modelType,
        context_window: contextWindow,
        cost_tier: costTier,
        supports_tools: supportsTools,
        supports_images: supportsImages,
        supports_thinking: supportsThinking,
        thinking_options: supportsThinking
          ? (enabledThinkingOptions.length > 0 ? enabledThinkingOptions : null)
          : null,
        extra_params: extraParams,
      };
      return editing
        ? modelsAdminApi.update(model.model_id, payload)
        : modelsAdminApi.create(providerName, payload);
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
            <select value={modelType} onChange={(e) => setModelType(e.target.value)} className={cn(inputCls, 'bg-white')}>
              <option value="text">text</option>
              <option value="embedding">embedding</option>
              <option value="reranker">reranker</option>
            </select>
          </Field>
          <Field label="成本档">
            <select value={costTier} onChange={(e) => setCostTier(e.target.value)} className={cn(inputCls, 'bg-white')}>
              <option value="cheap">cheap</option>
              <option value="mid">mid</option>
              <option value="expensive">expensive</option>
            </select>
          </Field>
        </div>
        <Field label="上下文窗口">
          <input
            type="number"
            value={contextWindow}
            onChange={(e) => setContextWindow(Number(e.target.value) || 0)}
            className={inputCls}
          />
        </Field>
        <Field label="能力">
          <div className="flex flex-wrap gap-2">
            <CapToggle label="工具调用" on={supportsTools} onClick={() => setSupportsTools((v) => !v)} />
            <CapToggle label="图片识别" on={supportsImages} onClick={() => setSupportsImages((v) => !v)} />
            <CapToggle label="思考模式" on={supportsThinking} onClick={() => setSupportsThinking((v) => !v)} />
          </div>
        </Field>
        <Field label="thinking_options (复选)">
          <div className="flex flex-wrap gap-2">
            {THINKING_LEVELS.map((level) => (
              <CapToggle
                key={level}
                label={level}
                on={thinkingOptions.includes(level)}
                onClick={() => toggleThinkingOption(level)}
              />
            ))}
          </div>
          {!supportsThinking && (
            <p className="mt-1 text-xs text-amber-600">当前未开启“思考模式”，保存时会清空 thinking_options。</p>
          )}
        </Field>
        <Field label="extra_params (JSON)">
          <textarea
            value={extraParamsText}
            onChange={(e) => setExtraParamsText(e.target.value)}
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
