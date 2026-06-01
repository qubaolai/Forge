import { Sparkles } from 'lucide-react';
import { useAuthStore } from '@/store/auth';

const SUGGESTIONS = [
  '帮我总结这份文档的要点',
  '解释一下这段代码的实现思路',
  '给我几条改进建议',
  '帮我润色下面这段文字',
];

/**
 * 新对话欢迎区：橙色徽标 + 问候语 + 示例提示词 chips。
 * 点击 chip 通过 onPick 把文本填入输入框（不直接发送）。
 */
export function ChatWelcome({ onPick }: { onPick: (text: string) => void }) {
  const user = useAuthStore((s) => s.user);
  const name = user?.name?.trim();

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-orange-100 to-orange-200">
        <Sparkles size={28} className="text-orange-500" />
      </div>
      <h2 className="mt-5 text-xl font-medium text-gray-800">
        {name ? `你好，${name}` : '开始新对话'}
      </h2>
      <p className="mt-1.5 text-sm text-gray-400">有什么可以帮你的吗？</p>

      <div className="mt-8 grid w-full max-w-md grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-left text-sm text-gray-600 transition-colors hover:border-orange-300 hover:bg-orange-50/50"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
