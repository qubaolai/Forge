import { SSEEvent } from '@/types';
import { useAuthStore } from '@/store/auth';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export interface SSEHandlers {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

/**
 * 基于 fetch + ReadableStream 的 SSE 客户端
 * 不用原生 EventSource 是因为它不支持自定义 Authorization header。
 *
 * 用法:
 *   const ctrl = openSSE('/chat/completions', { body: { ... } }, handlers);
 *   ctrl.abort(); // 中断
 */
export function openSSE(
  path: string,
  options: { method?: 'GET' | 'POST'; body?: unknown },
  handlers: SSEHandlers,
): { abort: () => void } {
  const controller = new AbortController();

  (async () => {
    try {
      const token = useAuthStore.getState().accessToken;
      const resp = await fetch(`${BASE_URL}${path}`, {
        method: options.method || 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'X-Client-Type': 'web',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
        credentials: 'include',
      });

      if (!resp.ok || !resp.body) {
        const text = await resp.text().catch(() => '');
        throw new Error(`SSE 连接失败: ${resp.status} ${text}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE 协议:事件以双换行分隔
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const raw of events) {
          const dataLine = raw
            .split('\n')
            .find((line) => line.startsWith('data:'));
          if (!dataLine) continue;
          const payload = dataLine.slice(5).trim();
          if (!payload || payload === '[DONE]') continue;
          try {
            const event = JSON.parse(payload) as SSEEvent;
            handlers.onEvent(event);
          } catch (err) {
            console.warn('SSE 解析失败:', payload, err);
          }
        }
      }
      handlers.onClose?.();
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        handlers.onClose?.();
        return;
      }
      handlers.onError?.(err as Error);
    }
  })();

  return {
    abort: () => controller.abort(),
  };
}
