import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { useAuthStore } from '@/store/auth';
import { ChatMessage } from '@/types';

export function UserMessage({ message }: { message: ChatMessage }) {
  const userName = useAuthStore((s) => s.user?.name);
  const initial = (userName?.[0] || '?').toUpperCase();
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="group flex justify-end gap-3">
      {/* hover 才显示的快速复制按钮, 紧贴气泡左侧 */}
      <button
        onClick={handleCopy}
        title="复制"
        className="self-center rounded-md p-1.5 text-gray-400 opacity-0 transition-opacity hover:bg-gray-100 hover:text-gray-700 group-hover:opacity-100"
      >
        {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
      </button>
      <div
        className="max-w-[75%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm
          bg-[#F5F5F5] px-4 py-2.5 text-[15px] leading-[1.7] text-gray-900 shadow-sm"
      >
        {message.content}
      </div>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-200 text-[13px] font-medium text-gray-700">
        {initial}
      </div>
    </div>
  );
}
