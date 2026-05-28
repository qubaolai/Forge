import { useRef, useState, KeyboardEvent } from 'react';
import { Send, Square, Brain } from 'lucide-react';
import type { ModelGroup } from '@/api';

export type ThinkingLevel = 'standard' | 'low' | 'medium' | 'high' | 'xhigh';

interface Props {
  onSend: (text: string) => void;
  onAbort?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  thinkingLevel?: ThinkingLevel;
  onThinkingLevelChange?: (value: ThinkingLevel) => void;
  // 模型选择
  selectedProvider: string;
  selectedModel: string;
  modelGroups: ModelGroup[];
  onModelChange: (provider: string, model: string) => void;
  thinkingEnabled: boolean;
  onThinkingChange: (enabled: boolean) => void;
}

const THINKING_LEVEL_LABELS: Record<ThinkingLevel, string> = {
  standard: '标准',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '超高',
};

export function ChatInput({
  onSend,
  onAbort,
  disabled,
  streaming,
  placeholder,
  thinkingLevel = 'standard',
  onThinkingLevelChange,
  selectedProvider,
  selectedModel,
  modelGroups,
  onModelChange,
  thinkingEnabled,
  onThinkingChange,
}: Props) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function autoResize() {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
  }

  function handleSubmit() {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue('');
    requestAnimationFrame(autoResize);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  }

  // 从选中模型元数据中获取思考能力
  const currentModel = modelGroups
    .find((group) => group.provider === selectedProvider)
    ?.models.find((m) => m.name === selectedModel);
  const thinkingMeta = currentModel?.thinking;
  const hasThinking = Boolean(currentModel?.supports_thinking);
  const thinkingOptions = (thinkingMeta?.options || []).filter(Boolean) as string[];
  const showThinkingLevel = hasThinking && thinkingOptions.length > 0 && onThinkingLevelChange;
  const selectedThinkingLevel = (
    thinkingOptions.includes(thinkingLevel) ? thinkingLevel : thinkingOptions[0]
  ) as ThinkingLevel;
  const selectedValue = `${selectedProvider}::${selectedModel}`;

  return (
    <div className="border-t bg-white px-6 py-4">
      <div className="mx-auto max-w-[760px]">
        <div
          className="flex items-end gap-2 rounded-2xl border px-3 py-2.5 transition-colors
            focus-within:border-gray-400 focus-within:shadow-sm"
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              autoResize();
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || '输入消息,Shift+Enter 换行'}
            rows={1}
            className="max-h-[200px] flex-1 resize-none bg-transparent py-1 text-[15px] leading-[1.7] outline-none placeholder:text-gray-400"
            disabled={disabled}
          />
          {streaming ? (
            <button
              onClick={onAbort}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 transition-colors hover:bg-gray-200"
              title="停止生成"
            >
              <Square size={15} className="fill-gray-700" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!value.trim() || disabled}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-black text-white transition-colors disabled:bg-gray-300 disabled:text-gray-500"
              title="发送 (Enter)"
            >
              <Send size={15} />
            </button>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[12px] text-gray-400">
          <div className="flex items-center gap-2">
            <select
              value={selectedValue}
              onChange={(e) => {
                const [provider, model] = e.target.value.split('::');
                if (provider && model) {
                  onModelChange(provider, model);
                }
              }}
              className="bg-transparent border rounded px-1.5 py-0.5 text-gray-500 cursor-pointer min-w-[120px]"
            >
              {modelGroups.length === 0 && <option value="">暂无可用模型</option>}
              {modelGroups.map((group) => (
                <optgroup key={group.provider} label={group.provider}>
                  {group.models.map((m) => (
                    <option key={`${group.provider}:${m.name}`} value={`${group.provider}::${m.name}`}>
                      {m.display_name || m.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            {hasThinking && (
              <label className="flex items-center gap-1 cursor-pointer text-gray-500">
                <input
                  type="checkbox"
                  checked={thinkingEnabled}
                  onChange={(e) => onThinkingChange(e.target.checked)}
                  className="rounded"
                />
                <span>思考</span>
              </label>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span>AI 回答可能不准确,请核实关键信息</span>
            {showThinkingLevel && (
              <label
                className="flex items-center gap-1.5 text-gray-500 hover:text-gray-700 cursor-pointer"
                title="思考强度: 标准/低/中/高/超高"
              >
                <Brain size={13} />
                <span>思考</span>
                <select
                  value={selectedThinkingLevel}
                  onChange={(e) => onThinkingLevelChange?.(e.target.value as ThinkingLevel)}
                  className="bg-transparent border-none outline-none cursor-pointer text-gray-700"
                >
                  {thinkingOptions.map((v: string) => (
                    <option key={v} value={v}>
                      {THINKING_LEVEL_LABELS[v as ThinkingLevel] || v}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
