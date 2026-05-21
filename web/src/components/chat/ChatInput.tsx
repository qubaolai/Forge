import { useRef, useState, KeyboardEvent } from 'react';
import { Send, Square, Brain } from 'lucide-react';

export type ReasoningEffort = 'high' | 'max';

interface Props {
  onSend: (text: string) => void;
  onAbort?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  reasoning?: ReasoningEffort;
  onReasoningChange?: (value: ReasoningEffort) => void;
}

const REASONING_LABELS: Record<ReasoningEffort, string> = {
  high: '标准',
  max: '深度',
};

export function ChatInput({
  onSend,
  onAbort,
  disabled,
  streaming,
  placeholder,
  reasoning = 'high',
  onReasoningChange,
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
          <span>AI 回答可能不准确,请核实关键信息</span>
          {onReasoningChange && (
            <label
              className="flex items-center gap-1.5 text-gray-500 hover:text-gray-700 cursor-pointer"
              title="思考强度: 标准 (high) 适合大多数场景, 深度 (max) 用于复杂推理 (DeepSeek thinking 模式始终开启)"
            >
              <Brain size={13} />
              <span>思考</span>
              <select
                value={reasoning}
                onChange={(e) => onReasoningChange(e.target.value as ReasoningEffort)}
                className="bg-transparent border-none outline-none cursor-pointer text-gray-700"
              >
                {(['high', 'max'] as ReasoningEffort[]).map((v) => (
                  <option key={v} value={v}>
                    {REASONING_LABELS[v]}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
