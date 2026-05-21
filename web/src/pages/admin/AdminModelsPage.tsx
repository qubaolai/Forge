import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MoreVertical, Plus, Cpu, Edit2, Trash2 } from 'lucide-react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { modelsApi } from '@/api';
import { ApiError, ModelEndpoint, ModelProvider } from '@/types';
import { toast } from '@/components/common/Toast';
import { cn } from '@/lib/utils';
import { confirm } from '@/components/common/ConfirmDialog';

const PROVIDERS: ModelProvider[] = ['openai', 'anthropic', 'ollama', 'vllm', 'custom'];
const CAPABILITIES = ['chat', 'embedding', 'vision', 'tool_use'] as const;

export default function AdminModelsPage() {
  const [editing, setEditing] = useState<ModelEndpoint | null>(null);
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-models'],
    queryFn: () => modelsApi.list(),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">模型管理</h1>
            <p className="text-sm text-gray-500 mt-1">
              配置可用的 LLM / Embedding 模型端点
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm
              bg-black text-white rounded-md hover:bg-gray-800"
          >
            <Plus size={14} />
            新增模型
          </button>
        </div>

        {isLoading ? (
          <div className="text-sm text-gray-400 py-12 text-center">加载中…</div>
        ) : !data || data.length === 0 ? (
          <div className="border-2 border-dashed border-gray-200 rounded-lg py-16 text-center">
            <Cpu className="mx-auto text-gray-300" size={40} />
            <p className="mt-3 text-sm text-gray-500">还没有模型</p>
            <button
              onClick={() => setCreating(true)}
              className="mt-4 text-sm text-blue-600 hover:underline"
            >
              添加第一个模型
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.map((m) => (
              <ModelCard key={m.id} model={m} onEdit={() => setEditing(m)} />
            ))}
          </div>
        )}
      </div>

      {(creating || editing) && (
        <EditDialog
          model={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function ModelCard({ model, onEdit }: { model: ModelEndpoint; onEdit: () => void }) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => modelsApi.remove(model.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-models'] });
      toast.success('已删除');
    },
    onError: (e) => toast.error((e as ApiError).message || '删除失败'),
  });

  return (
    <div className="border rounded-lg p-4 bg-white">
      <div className="flex items-start justify-between mb-2">
        <div className="w-9 h-9 rounded-lg bg-purple-50 flex items-center justify-center">
          <Cpu className="text-purple-600" size={18} />
        </div>
        <div className="flex items-center gap-1">
          <span
            className={cn(
              'inline-flex px-1.5 py-0.5 rounded text-[10px]',
              model.enabled
                ? 'bg-green-50 text-green-700'
                : 'bg-gray-100 text-gray-500',
            )}
          >
            {model.enabled ? '启用' : '停用'}
          </span>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="p-1 rounded hover:bg-gray-100">
                <MoreVertical size={14} />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                className="bg-white border rounded-md shadow-md py-1 text-sm min-w-[120px]"
              >
                <DropdownMenu.Item
                  onSelect={onEdit}
                  className="px-3 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-gray-50 outline-none"
                >
                  <Edit2 size={13} /> 编辑
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  onSelect={async () => {
                    if (await confirm({ message: `确定删除模型 ${model.name}?`, confirmLabel: '删除', danger: true })) remove.mutate();
                  }}
                  className="px-3 py-1.5 flex items-center gap-2 cursor-pointer hover:bg-red-50 text-red-600 outline-none"
                >
                  <Trash2 size={13} /> 删除
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>
      <h3 className="font-medium text-sm mb-1 truncate">{model.name}</h3>
      <div className="text-xs text-gray-500 mb-2">
        {model.provider} · {model.model_name}
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        {model.capabilities.map((c) => (
          <span
            key={c}
            className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-600"
          >
            {c}
          </span>
        ))}
      </div>
      <div className="text-[11px] text-gray-400">
        ctx {model.context_window} ·{' '}
        {model.api_key_set ? '已配置 API Key' : '无 API Key'}
      </div>
    </div>
  );
}

function EditDialog({
  model,
  onClose,
}: {
  model: ModelEndpoint | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const editing = !!model;
  const [name, setName] = useState(model?.name || '');
  const [provider, setProvider] = useState<ModelProvider>(model?.provider || 'openai');
  const [baseUrl, setBaseUrl] = useState(model?.base_url || '');
  const [modelName, setModelName] = useState(model?.model_name || '');
  const [apiKey, setApiKey] = useState('');
  const [ctxWindow, setCtxWindow] = useState(model?.context_window ?? 8192);
  const [capabilities, setCapabilities] = useState<string[]>(
    model?.capabilities || ['chat'],
  );
  const [enabled, setEnabled] = useState(model?.enabled ?? true);

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        name: name.trim(),
        provider,
        base_url: baseUrl.trim(),
        model_name: modelName.trim(),
        context_window: ctxWindow,
        capabilities: capabilities as ModelEndpoint['capabilities'],
        enabled,
        ...(apiKey ? { api_key: apiKey } : {}),
      };
      return editing
        ? modelsApi.update(model.id, payload)
        : modelsApi.create(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-models'] });
      toast.success(editing ? '已保存' : '已新增');
      onClose();
    },
    onError: (e) => toast.error((e as ApiError).message || '保存失败'),
  });

  const canSubmit = name.trim().length > 0 && modelName.trim().length > 0;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-lg shadow-xl w-full max-w-lg p-5 max-h-[90vh] overflow-y-auto"
      >
        <h2 className="text-base font-semibold mb-4">
          {editing ? '编辑模型' : '新增模型'}
        </h2>
        <div className="space-y-3">
          <Field label="显示名称 *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如:GPT-4o"
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Provider">
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value as ModelProvider)}
                className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 bg-white"
              >
                {PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Model Name *">
              <input
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder="gpt-4o-mini"
                className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 font-mono"
              />
            </Field>
          </div>
          <Field label="Base URL">
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 font-mono"
            />
          </Field>
          <Field
            label={
              editing
                ? `API Key (${model.api_key_set ? '已存在,留空保持不变' : '未配置'})`
                : 'API Key'
            }
          >
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={editing ? '留空保持不变' : 'sk-...'}
              className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400 font-mono"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Context Window">
              <input
                type="number"
                value={ctxWindow}
                onChange={(e) => setCtxWindow(Number(e.target.value) || 0)}
                className="w-full border rounded-md px-3 py-1.5 text-sm outline-none focus:border-gray-400"
              />
            </Field>
            <Field label="启用">
              <button
                onClick={() => setEnabled(!enabled)}
                className={cn(
                  'w-full px-3 py-1.5 text-sm rounded-md border',
                  enabled
                    ? 'border-green-300 bg-green-50 text-green-700'
                    : 'border-gray-300 bg-white text-gray-600',
                )}
              >
                {enabled ? '已启用' : '已停用'}
              </button>
            </Field>
          </div>
          <Field label="能力">
            <div className="flex flex-wrap gap-2">
              {CAPABILITIES.map((c) => {
                const on = capabilities.includes(c);
                return (
                  <button
                    key={c}
                    onClick={() =>
                      setCapabilities((prev) =>
                        on ? prev.filter((x) => x !== c) : [...prev, c],
                      )
                    }
                    className={cn(
                      'px-2.5 py-1 text-xs rounded-md border',
                      on
                        ? 'border-gray-900 bg-gray-900 text-white'
                        : 'border-gray-200 hover:bg-gray-50',
                    )}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          </Field>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50"
          >
            取消
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={!canSubmit || save.isPending}
            className="px-3 py-1.5 text-sm bg-black text-white rounded-md hover:bg-gray-800 disabled:opacity-50"
          >
            {save.isPending ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{label}</label>
      {children}
    </div>
  );
}
