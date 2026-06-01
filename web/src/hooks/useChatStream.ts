import { useCallback, useRef, useState } from 'react';
import { openSSE } from '@/api/sse';
import { chatApi } from '@/api';
import {
  ChatMessage,
  Citation,
  ContextUsage,
  MessageStatus,
  SSEEvent,
  ToolCall,
} from '@/types';

interface UseChatStreamOptions {
  onComplete?: (message: ChatMessage) => void;
  onError?: (error: Error) => void;
  onSessionCreated?: (sessionId: string, title: string) => void;
  onSessionRenamed?: (sessionId: string, title: string) => void;
}

/** 后端 ChatCompletionIn.model_options 结构。
 *
 * provider / model 必传；
 * thinking 表示是否开启思考，thinking_level 表示统一强度档位。
 */
export interface ModelOptions {
  provider: string;
  model: string;
  thinking?: boolean;
  thinking_level?: 'standard' | 'low' | 'medium' | 'high' | 'xhigh';
}

/**
 * 处理一次完整的流式对话:
 *  - 创建 assistant 消息占位
 *  - 接收 SSE 事件累积内容
 *  - 暴露 abort 中断
 *  - 暴露 reset 清空当前消息 (切换会话时使用, 避免泄露到新会话)
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const [streaming, setStreaming] = useState(false);
  const [current, setCurrent] = useState<ChatMessage | null>(null);
  // 当前会话的上下文占用快照 (分层), 由 SSE context_usage 事件更新
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  const ctrlRef = useRef<{ abort: () => void } | null>(null);
  // 后端 message_id (来自 message_start 事件), 用于调用 /chat/stop 停掉 LLM
  const activeMessageIdRef = useRef<string | null>(null);

  // Always keep a ref to the latest options so SSE handlers running across navigation
  // can call the correct callbacks (e.g. onComplete with the real sessionId).
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const send = useCallback(
    (
      sessionId: string | null,
      message: string,
      attachments?: { file_id: string; type: string }[],
      modelOptions?: ModelOptions,
    ) => {
      // 占位的 assistant 消息(stream 期间逐步填充)
      const draft: ChatMessage = {
        id: 'tmp_' + Date.now(),
        session_id: sessionId || '',
        role: 'assistant',
        content: '',
        status: 'pending' as MessageStatus,
        citations: [],
        tool_calls: [],
        created_at: new Date().toISOString(),
      };
      setCurrent(draft);
      setStreaming(true);

      const body: Record<string, unknown> = { message, attachments };
      if (sessionId) body.session_id = sessionId;
      if (modelOptions && Object.keys(modelOptions).length > 0) {
        body.model_options = modelOptions;
      }

      const ctrl = openSSE(
        '/chat/completions',
        { body },
        {
          onEvent: (e: SSEEvent) => {
            // 诊断日志：观察流式事件是否真正到达前端
            if (import.meta.env.DEV) console.debug('[SSE]', e.type, e);

            if (e.type === 'session_created') {
              setCurrent((prev) => (prev ? { ...prev, session_id: e.session_id } : prev));
              optionsRef.current.onSessionCreated?.(e.session_id, e.title);
              return;
            }

            if (e.type === 'session_renamed') {
              optionsRef.current.onSessionRenamed?.(e.session_id, e.title);
              return;
            }

            if (e.type === 'message_resumed') {
              // resume 流: 该 message 的旧内容已在前端, 直接在此基础上接 delta
              setCurrent((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  id: e.message_id,
                  session_id: e.session_id,
                  status: 'streaming',
                };
              });
              activeMessageIdRef.current = e.message_id;
              return;
            }

            if (e.type === 'context_usage') {
              // 上下文占用快照 (分层): 更新顶部圆环
              setContextUsage({
                context_window: e.context_window,
                input_tokens: e.input_tokens,
                total_ratio: e.total_ratio,
                layers: e.layers,
              });
              return;
            }

            if (e.type === 'compaction_started' || e.type === 'compaction_done') {
              // 上下文压缩生命周期事件: 前端可据此显示提示, 当前静默处理
              if (import.meta.env.DEV) console.debug('[SSE]', e.type, e);
              return;
            }

            setCurrent((prev) => {
              if (!prev) return prev;
              const next = { ...prev };
              switch (e.type) {
                case 'message_start':
                  next.id = e.message_id;
                  next.status = 'streaming';
                  activeMessageIdRef.current = e.message_id;
                  break;
                case 'delta':
                  next.content = (prev.content || '') + e.content;
                  next.status = 'streaming';
                  break;
                case 'reasoning_delta':
                  next.reasoning_content = (prev.reasoning_content || '') + e.content;
                  next.status = 'streaming';
                  break;
                case 'reasoning_end':
                  if (e.reasoning_duration_ms != null) {
                    next.reasoning_duration_ms = e.reasoning_duration_ms;
                  }
                  break;
                case 'tool_call':
                  next.tool_calls = [...(prev.tool_calls || []), e.tool_call];
                  break;
                case 'tool_result': {
                  next.tool_calls = (prev.tool_calls || []).map((tc): ToolCall =>
                    tc.id === e.tool_call_id
                      ? { ...tc, status: e.status, result: e.result }
                      : tc,
                  );
                  break;
                }
                case 'citations':
                  next.citations = mergeCitations(prev.citations || [], e.citations);
                  break;
                case 'done':
                  next.status = 'done';
                  next.usage = e.usage;
                  if (e.reasoning_duration_ms != null) {
                    next.reasoning_duration_ms = e.reasoning_duration_ms;
                  }
                  break;
                case 'task_partial':
                  next.status = e.reason === 'aborted' ? 'aborted' : 'partial';
                  next.content = e.content_so_far;
                  next.tool_calls = (e.tool_calls_so_far as ToolCall[]) || prev.tool_calls;
                  if (e.usage) next.usage = e.usage;
                  if (e.reasoning_duration_ms != null) {
                    next.reasoning_duration_ms = e.reasoning_duration_ms;
                  }
                  break;
                case 'error':
                  next.status = 'error';
                  next.error_message = e.message;
                  break;
              }
              return next;
            });

            if (e.type === 'done') {
              setStreaming(false);
              ctrlRef.current = null;
              activeMessageIdRef.current = null;
              setCurrent((m) => {
                if (m) optionsRef.current.onComplete?.(m);
                return m;
              });
            } else if (e.type === 'task_partial') {
              setStreaming(false);
              ctrlRef.current = null;
              activeMessageIdRef.current = null;
              setCurrent((m) => {
                if (m) optionsRef.current.onComplete?.(m);
                return m;
              });
            } else if (e.type === 'error') {
              setStreaming(false);
              ctrlRef.current = null;
              activeMessageIdRef.current = null;
              optionsRef.current.onError?.(new Error(e.message));
            }
          },
          onError: (err) => {
            setStreaming(false);
            ctrlRef.current = null;
            setCurrent((prev) =>
              prev ? { ...prev, status: 'error', error_message: err.message } : prev,
            );
            optionsRef.current.onError?.(err);
          },
        },
      );
      ctrlRef.current = ctrl;
    },
    // optionsRef is stable; no deps needed
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const abort = useCallback(() => {
    // 1) 先告诉后端停 LLM (fire-and-forget, 拿不到 message_id 就跳过)
    //    必须在 fetch 切断前发, 否则后端单纯靠 socket close 检测要等到下次 yield 才生效.
    const mid = activeMessageIdRef.current;
    if (mid) {
      chatApi.stop(mid).catch(() => {
        // 网络抖动或后端已结束, 都不影响本地状态, 静默吞掉
      });
    }
    // 2) 切断 SSE 连接, 释放浏览器资源
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    activeMessageIdRef.current = null;
    setStreaming(false);
    setCurrent((prev) => (prev ? { ...prev, status: 'aborted' } : prev));
  }, []);

  /** 继续生成未完成的消息 (partial / aborted). */
  const resume = useCallback((message: ChatMessage) => {
    // 以已有消息为基础, 后端返回的新 delta 直接追加到已有 content 之后
    setCurrent({ ...message, status: 'streaming' });
    setStreaming(true);

    const ctrl = openSSE(
      '/chat/resume',
      { body: { message_id: message.id } },
      {
        onEvent: (e: SSEEvent) => {
          if (import.meta.env.DEV) console.debug('[SSE]', e.type, e);

          if (e.type === 'message_resumed') {
            activeMessageIdRef.current = e.message_id;
            // 续写开始 → 状态回到 streaming, 让停止按钮重新可用,
            // 避免被旧的 aborted/partial 状态卡住.
            setCurrent((prev) =>
              prev ? { ...prev, status: 'streaming' as MessageStatus } : prev,
            );
            setStreaming(true);
            return;
          }

          if (e.type === 'context_usage') {
            setContextUsage({
              context_window: e.context_window,
              input_tokens: e.input_tokens,
              total_ratio: e.total_ratio,
              layers: e.layers,
            });
            return;
          }

          if (e.type === 'compaction_started' || e.type === 'compaction_done') {
            if (import.meta.env.DEV) console.debug('[SSE]', e.type, e);
            return;
          }

          setCurrent((prev) => {
            if (!prev) return prev;
            const next = { ...prev };
            switch (e.type) {
              case 'delta':
                next.content = (prev.content || '') + e.content;
                next.status = 'streaming';
                break;
              case 'reasoning_delta':
                next.reasoning_content = (prev.reasoning_content || '') + e.content;
                next.status = 'streaming';
                break;
              case 'tool_call':
                next.tool_calls = [...(prev.tool_calls || []), e.tool_call];
                break;
              case 'tool_result': {
                next.tool_calls = (prev.tool_calls || []).map((tc): ToolCall =>
                  tc.id === e.tool_call_id
                    ? { ...tc, status: e.status, result: e.result }
                    : tc,
                );
                break;
              }
              case 'citations':
                next.citations = mergeCitations(prev.citations || [], e.citations);
                break;
              case 'done':
                next.status = 'done';
                next.usage = e.usage;
                if (e.reasoning_duration_ms != null) {
                  next.reasoning_duration_ms = e.reasoning_duration_ms;
                }
                break;
              case 'task_partial':
                next.status = e.reason === 'aborted' ? 'aborted' : 'partial';
                next.content = e.content_so_far;
                next.tool_calls = (e.tool_calls_so_far as ToolCall[]) || prev.tool_calls;
                if (e.usage) next.usage = e.usage;
                if (e.reasoning_duration_ms != null) {
                  next.reasoning_duration_ms = e.reasoning_duration_ms;
                }
                break;
              case 'error':
                next.status = 'error';
                next.error_message = e.message;
                break;
            }
            return next;
          });

          if (e.type === 'done') {
            setStreaming(false);
            ctrlRef.current = null;
            activeMessageIdRef.current = null;
            setCurrent((m) => {
              if (m) optionsRef.current.onComplete?.(m);
              return m;
            });
          } else if (e.type === 'task_partial') {
            setStreaming(false);
            ctrlRef.current = null;
            activeMessageIdRef.current = null;
            setCurrent((m) => {
              if (m) optionsRef.current.onComplete?.(m);
              return m;
            });
          } else if (e.type === 'error') {
            setStreaming(false);
            ctrlRef.current = null;
            activeMessageIdRef.current = null;
            optionsRef.current.onError?.(new Error(e.message));
          }
        },
        onError: (err) => {
          setStreaming(false);
          ctrlRef.current = null;
          setCurrent((prev) =>
            prev ? { ...prev, status: 'error', error_message: err.message } : prev,
          );
          optionsRef.current.onError?.(err);
        },
      },
    );
    ctrlRef.current = ctrl;
  }, []);

  /** 完全清空 streaming 状态 (切换会话/卸载场景使用)。 */
  const reset = useCallback(() => {
    const mid = activeMessageIdRef.current;
    if (mid) {
      chatApi.stop(mid).catch(() => undefined);
    }
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    activeMessageIdRef.current = null;
    setStreaming(false);
    setCurrent(null);
    setContextUsage(null);
  }, []);

  return { send, abort, resume, reset, streaming, current, contextUsage };
}

function mergeCitations(existing: Citation[], incoming: Citation[]): Citation[] {
  const seen = new Set(existing.map((c) => c.chunk_id));
  return [...existing, ...incoming.filter((c) => !seen.has(c.chunk_id))];
}
