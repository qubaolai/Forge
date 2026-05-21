import { AgentDraft } from './draft';
import { Field, Slider, Toggle } from './FormControls';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

export function AdvancedTab({ draft, update }: Props) {
  function patchAdv(p: Partial<AgentDraft['advanced']>) {
    update({ advanced: { ...draft.advanced, ...p } });
  }
  return (
    <div className="max-w-xl">
      <Field
        label={`上下文窗口: ${draft.advanced.context_window} tokens`}
        hint="累计对话历史最多保留多少 token"
      >
        <Slider
          value={draft.advanced.context_window}
          min={1024} max={32768} step={512}
          onChange={(v) => patchAdv({ context_window: v })}
        />
      </Field>

      <Field
        label={`超时: ${draft.advanced.timeout_seconds} 秒`}
        hint="单次请求最长等待时间"
      >
        <Slider
          value={draft.advanced.timeout_seconds}
          min={10} max={300} step={5}
          onChange={(v) => patchAdv({ timeout_seconds: v })}
        />
      </Field>

      <Field label={`最大重试: ${draft.advanced.max_retries} 次`}>
        <Slider
          value={draft.advanced.max_retries}
          min={0} max={5}
          onChange={(v) => patchAdv({ max_retries: v })}
        />
      </Field>

      <div className="mt-4">
        <Toggle
          checked={draft.advanced.enable_streaming}
          onChange={(v) => patchAdv({ enable_streaming: v })}
          label="启用流式输出 (推荐开启)"
        />
      </div>
    </div>
  );
}
