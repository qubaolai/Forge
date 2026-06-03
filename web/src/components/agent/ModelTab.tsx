import { useQuery } from '@tanstack/react-query';
import { modelsApi } from '@/api';
import { AgentDraft } from './draft';
import { Field, Slider } from './FormControls';
import { cn } from '@/lib/utils';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

export function ModelTab({ draft, update }: Props) {
  const { data: models, isLoading } = useQuery({
    queryKey: ['models'],
    queryFn: () => modelsApi.list(),
  });

  function patchModel(p: Partial<AgentDraft['model']>) {
    update({ model: { ...draft.model, ...p } });
  }

  return (
    <div className="max-w-xl">
      <Field label="模型">
        {isLoading ? (
          <div className="text-sm text-gray-400">加载模型列表…</div>
        ) : !models || models.length === 0 ? (
          <div className="text-sm text-gray-500 border rounded-md p-3">
            还没有可用的模型,请管理员先在 <span className="font-mono">/admin/models</span> 添加。
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {models.map((m) => (
              <div
                key={m.model_id}
                onClick={() => patchModel({ model_id: m.model_id })}
                className={cn(
                  'border rounded-md p-3 cursor-pointer transition-colors',
                  draft.model.model_id === m.model_id
                    ? 'border-gray-900 bg-gray-50'
                    : 'border-gray-200 hover:border-gray-400',
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">{m.display_name || m.name}</div>
                  <div className="text-[10px] uppercase text-gray-400">{m.provider}</div>
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {m.name} · 上下文 {(m.config.context_window || 0).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        )}
      </Field>

      <Field label={`Temperature: ${draft.model.temperature.toFixed(2)}`} hint="0 = 最确定,2 = 最随机">
        <Slider
          value={draft.model.temperature}
          min={0}
          max={2}
          step={0.05}
          onChange={(v) => patchModel({ temperature: v })}
        />
      </Field>

      <Field label={`Max Tokens: ${draft.model.max_tokens}`} hint="单次回复的最大 token 数">
        <Slider
          value={draft.model.max_tokens}
          min={256}
          max={8192}
          step={128}
          onChange={(v) => patchModel({ max_tokens: v })}
        />
      </Field>

      <Field label={`Top P: ${draft.model.top_p.toFixed(2)}`} hint="核采样概率,通常保持 0.9">
        <Slider
          value={draft.model.top_p}
          min={0}
          max={1}
          step={0.05}
          onChange={(v) => patchModel({ top_p: v })}
        />
      </Field>
    </div>
  );
}
