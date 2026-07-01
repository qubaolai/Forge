import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowDown, Sparkles } from 'lucide-react';
import { ChatMessage, ChatFileMeta, Citation } from '@/types';
import { UserMessage } from './UserMessage';
import { AssistantMessage } from './AssistantMessage';

interface Props {
  messages: ChatMessage[];
  onCitationClick?: (citation: Citation, message: ChatMessage) => void;
  onRegenerate?: (messageId: string) => void;
  onResume?: (messageId: string) => void;
  onFilePreview?: (file: ChatFileMeta) => void;
}

/**
 * 滚动行为模型:
 *  - 单一可信源 = `pinnedToBottom`, 由 IntersectionObserver 观察底部哨兵自动维护
 *  - 首次有内容时 useLayoutEffect 瞬间 scrollTop=scrollHeight, 避免首屏从顶部滚下来的抖动
 *  - messages 变化时, 仅当 `pinnedToBottom===true` 才平滑 scrollIntoView (自动跟随)
 *  - 用户向上滚 > 60px (哨兵脱出 rootMargin 扩展区) → observer 立刻 false → 跟随停止
 *  - 用户滚回底部 → 哨兵进视口 → 状态自动恢复 true → 按钮自动隐藏
 *  - sessionId 切换由 ChatPage 通过 key 强制 remount, 内部状态自然重置
 */
export function MessageList({ messages, onCitationClick, onRegenerate, onResume, onFilePreview }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const firstScrollDoneRef = useRef(false);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  // 首次有消息时瞬间跳到底, 让用户直接看到最新内容 (而不是从顶部 smooth 滚下来)
  useLayoutEffect(() => {
    if (firstScrollDoneRef.current) return;
    if (messages.length === 0) return;
    const c = containerRef.current;
    if (!c) return;
    c.scrollTop = c.scrollHeight;
    firstScrollDoneRef.current = true;
  }, [messages]);

  // IntersectionObserver: 哨兵可见 ↔ 用户在底部 (rootMargin 让"接近底部 60px"内仍算粘底)
  useEffect(() => {
    const c = containerRef.current;
    const b = bottomRef.current;
    if (!c || !b) return;
    const io = new IntersectionObserver(
      ([entry]) => setPinnedToBottom(entry.isIntersecting),
      { root: c, rootMargin: '0px 0px 60px 0px', threshold: 0 },
    );
    io.observe(b);
    return () => io.disconnect();
  }, []);

  // 自动跟随: 内容变化时, 粘底状态下平滑滚到底
  useEffect(() => {
    if (!pinnedToBottom) return;
    if (!firstScrollDoneRef.current) return;  // 让 useLayoutEffect 先完成首次定位
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, pinnedToBottom]);

  const handleScrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, []);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-6 text-gray-400">
        <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-orange-100 to-orange-200">
          <Sparkles size={24} className="text-orange-500" />
        </div>
        <div className="mb-1 text-[16px] text-gray-700">开始一段新对话</div>
        <div className="text-[13px] text-gray-400">输入下方消息或按 Enter 发送</div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={containerRef} className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto flex max-w-[760px] flex-col gap-7">
          {messages.map((m) => (
            <div key={m.id}>
              {m.role === 'user' ? (
                <UserMessage message={m} onFilePreview={onFilePreview} />
              ) : (
                <AssistantMessage
                  message={m}
                  onCitationClick={onCitationClick}
                  onRegenerate={onRegenerate ? () => onRegenerate(m.id) : undefined}
                  onResume={onResume ? () => onResume(m.id) : undefined}
                  onFilePreview={onFilePreview}
                />
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>
      {!pinnedToBottom && (
        <button
          onClick={handleScrollToBottom}
          title="回到底部"
          aria-label="回到底部"
          className="absolute bottom-4 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-600 shadow-md transition-all hover:bg-gray-50 hover:text-gray-900"
        >
          <ArrowDown size={16} />
        </button>
      )}
    </div>
  );
}
