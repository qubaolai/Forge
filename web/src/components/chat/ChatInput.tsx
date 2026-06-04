import { useEffect, useRef, useState, KeyboardEvent } from 'react';
import { Send, Square, Brain } from 'lucide-react';
import type { ModelGroup } from '@/types';
import { cn } from '@/lib/utils';

export type ThinkingLevel = 'low' | 'medium' | 'high' | 'xhigh';

interface Props {
  onSend: (text: string) => void;
  onAbort?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  thinkingLevel?: ThinkingLevel;
  onThinkingLevelChange?: (value: ThinkingLevel) => void;
  // 外部预填文本（如点击示例提示词），写入输入框并聚焦
  prefill?: string;
  // 模型选择
  selectedProvider: string;
  selectedModel: string;
  modelGroups: ModelGroup[];
  onModelChange: (provider: string, model: string) => void;
  thinkingEnabled: boolean;
  onThinkingChange: (enabled: boolean) => void;
}

const THINKING_LEVEL_LABELS: Record<ThinkingLevel, string> = {
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
  thinkingLevel = 'medium',
  onThinkingLevelChange,
  prefill,
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

  // 外部预填（点击示例提示词）：写入输入框并聚焦
  useEffect(() => {
    if (!prefill) return;
    setValue(prefill);
    const ta = textareaRef.current;
    if (ta) {
      ta.focus();
      requestAnimationFrame(autoResize);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

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
  const hasThinking = Boolean(currentModel?.config?.capabilities?.includes('thinking'));
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
              <div className="flex items-center gap-1.5">
                {/* 思考开关（药丸式） */}
                <button
                  type="button"
                  onClick={() => onThinkingChange(!thinkingEnabled)}
                  className={cn(
                    'flex items-center gap-1 rounded-full border px-2 py-0.5 transition-colors',
                    thinkingEnabled
                      ? 'border-orange-200 bg-orange-50 text-orange-600'
                      : 'border-gray-200 text-gray-400 hover:text-gray-600',
                  )}
                  title={thinkingEnabled ? '已开启思考' : '已关闭思考'}
                >
                  <Brain size={13} />
                  <span>思考</span>
                </button>
                {/* 思考强度（分段控件）：仅开启且模型支持档位时显示 */}
                {thinkingEnabled && showThinkingLevel && (
                  <div className="flex items-center gap-0.5 rounded-full bg-gray-100 p-0.5">
                    {thinkingOptions.map((v) => (
                      <button
                        key={v}
                        type="button"
                        onClick={() => onThinkingLevelChange?.(v as ThinkingLevel)}
                        className={cn(
                          'rounded-full px-2 py-0.5 text-[11px] transition-colors',
                          selectedThinkingLevel === v
                            ? 'bg-white text-gray-800 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700',
                        )}
                      >
                        {THINKING_LEVEL_LABELS[v as ThinkingLevel] || v}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <span className="shrink-0 pl-2">AI 回答可能不准确,请核实关键信息</span>
        </div>
      </div>
    </div>
  );
}
