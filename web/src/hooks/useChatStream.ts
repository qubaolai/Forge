import { useCallback, useRef, useState } from 'react';
import { openSSE } from '@/api/sse';
import { chatApi } from '@/api';
import {
  AdaptiveRunEvent,
  ChatMessage,
  Citation,
  MessageStatus,
  RunStatus,
  SSEEvent,
  TaskOptionsInput,
  ToolCall,
} from '@/types';

interface UseChatStreamOptions {
  onComplete?: (message: ChatMessage) => void;
  onError?: (error: Error) => void;
  onSessionCreated?: (sessionId: string, title: string) => void;
  onSessionRenamed?: (sessionId: string, title: string) => void;
}

/** 后端 ChatCompletionIn.model_options 结构 (透传到 LLM provider).
 *
 * 注: thinking 由后端 DeepSeekLLM 默认开启, 前端不再传该字段.
 * reasoning_effort 取值对齐 DeepSeek 官方文档: high (默认) | max (复杂推理).
 */
export interface ModelOptions {
  reasoning_effort?: 'high' | 'max';
}

export interface ChatSendOptions {
  mode?: 'auto' | 'chat' | 'task';
  taskOptions?: TaskOptionsInput;
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
      agentId?: string,
      attachments?: { file_id: string; type: string }[],
      modelOptions?: ModelOptions,
      sendOptions?: ChatSendOptions,
    ) => {
      const isTaskMode = sendOptions?.mode === 'task';
      // 占位的 assistant 消息(stream 期间逐步填充)
      const draft: ChatMessage = {
        id: 'tmp_' + Date.now(),
        session_id: sessionId || '',
        role: 'assistant',
        content: isTaskMode ? '任务已提交，正在等待后端事件…' : '',
        status: 'pending' as MessageStatus,
        citations: [],
        tool_calls: [],
        created_at: new Date().toISOString(),
        adaptive_run: isTaskMode ? { artifact_ids: [], events: [] } : undefined,
      };
      setCurrent(draft);
      setStreaming(true);

      const body: Record<string, unknown> = { message, attachments };
      if (sessionId) body.session_id = sessionId;
      if (agentId) body.agent_id = agentId;
      if (modelOptions && Object.keys(modelOptions).length > 0) {
        body.model_options = modelOptions;
      }
      if (sendOptions?.mode) {
        body.mode = sendOptions.mode;
      }
      if (sendOptions?.taskOptions) {
        body.task_options = sendOptions.taskOptions;
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

            if (e.type === 'compaction_started' || e.type === 'compaction_done') {
              // 上下文压缩生命周期事件: 前端可据此显示提示, 当前静默处理
              if (import.meta.env.DEV) console.debug('[SSE]', e.type, e);
              return;
            }

            setCurrent((prev) => {
              if (!prev) return prev;
              const next = { ...prev };
              if (isAdaptiveRunEvent(e)) {
                const runEvent = toAdaptiveRunEvent(e);
                const previousRun = prev.adaptive_run || { artifact_ids: [], events: [] };
                next.adaptive_run = {
                  ...previousRun,
                  run_id: e.run_id || previousRun.run_id,
                  status: statusFromAdaptiveEvent(e, previousRun.status),
                  artifact_ids: previousRun.artifact_ids,
                  events: runEvent
                    ? [...previousRun.events, runEvent]
                    : previousRun.events,
                };
                next.content = formatAdaptiveRunContent(next.adaptive_run);
                next.status = 'streaming';
                return next;
              }
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
                case 'run.done': {
                  const previousRun = prev.adaptive_run || { artifact_ids: [], events: [] };
                  next.adaptive_run = {
                    ...previousRun,
                    run_id: e.run_id,
                    status: e.status,
                    artifact_ids: e.artifact_ids || [],
                  };
                  next.content = formatAdaptiveRunContent(next.adaptive_run);
                  next.status = e.status === 'completed' ? 'done' : 'error';
                  if (e.status !== 'completed') {
                    next.error_message = `任务结束状态：${runStatusLabel(e.status)}`;
                  }
                  break;
                }
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
            } else if (e.type === 'run.done') {
              setStreaming(false);
              ctrlRef.current = null;
              activeMessageIdRef.current = null;
              setCurrent((m) => {
                if (m) optionsRef.current.onComplete?.(m);
                return m;
              });
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
                break;
              case 'reasoning_delta':
                next.reasoning_content = (prev.reasoning_content || '') + e.content;
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
  }, []);

  return { send, abort, resume, reset, streaming, current };
}

function mergeCitations(existing: Citation[], incoming: Citation[]): Citation[] {
  const seen = new Set(existing.map((c) => c.chunk_id));
  return [...existing, ...incoming.filter((c) => !seen.has(c.chunk_id))];
}

function isAdaptiveRunEvent(e: SSEEvent): e is Extract<SSEEvent, { run_id: string; payload: Record<string, unknown> }> {
  return 'run_id' in e && 'payload' in e && typeof e.run_id === 'string' && typeof e.payload === 'object';
}

function toAdaptiveRunEvent(e: Extract<SSEEvent, { run_id: string; payload: Record<string, unknown> }>): AdaptiveRunEvent | null {
  if (!e.run_id) return null;
  return {
    id: e.event_id || `${e.type}_${Date.now()}_${Math.random()}`,
    run_id: e.run_id,
    type: e.type,
    ts: e.ts || new Date().toISOString(),
    payload: e.payload || {},
  };
}

function statusFromAdaptiveEvent(
  e: Extract<SSEEvent, { run_id: string; payload: Record<string, unknown> }>,
  fallback?: RunStatus,
): RunStatus | undefined {
  if (e.type === 'run.status_changed') {
    const toStatus = e.payload.to_status;
    if (typeof toStatus === 'string') return toStatus as RunStatus;
  }
  if (e.type === 'run.completed') return 'completed';
  if (e.type === 'run.failed') return 'failed';
  if (e.type === 'run.blocked') return 'blocked';
  if (e.type === 'run.aborted') return 'aborted';
  if (e.type === 'run.created') return 'created';
  if (e.type === 'run.started') return 'planning';
  return fallback;
}

function formatAdaptiveRunContent(run: ChatMessage['adaptive_run']): string {
  if (!run) return '任务执行中…';
  const lines = [
    `Adaptive Run${run.run_id ? ` \`${run.run_id}\`` : ''}`,
    '',
    `状态：${runStatusLabel(run.status || 'created')}`,
    `事件数：${run.events.length}`,
  ];
  if (run.artifact_ids.length > 0) {
    lines.push(`产物数：${run.artifact_ids.length}`);
  }
  const lastEvents = run.events.slice(-6);
  if (lastEvents.length > 0) {
    lines.push('', '最近事件：');
    for (const evt of lastEvents) {
      lines.push(`- ${eventLabel(evt.type)} ${formatEventPayload(evt.payload)}`);
    }
  }
  return lines.join('\n');
}

function runStatusLabel(status: RunStatus): string {
  const labels: Record<RunStatus, string> = {
    created: '已创建',
    planning: '规划中',
    validating: '校验中',
    executing: '执行中',
    integrating: '集成中',
    verifying: '验证中',
    completed: '已完成',
    failed: '失败',
    blocked: '阻塞',
    aborted: '已中止',
  };
  return labels[status] || status;
}

function eventLabel(type: string): string {
  const labels: Record<string, string> = {
    'run.created': '创建运行',
    'run.started': '启动运行',
    'run.status_changed': '状态变更',
    'run.completed': '运行完成',
    'run.failed': '运行失败',
    'run.blocked': '运行阻塞',
    'task.started': '任务开始',
    'task.completed': '任务完成',
    'task.failed': '任务失败',
    'task.skipped': '任务跳过',
    'wave.started': 'Wave 开始',
    'wave.completed': 'Wave 完成',
    'artifact.created': '产物创建',
    'plan.created': '计划创建',
    'plan.validated': '计划通过',
    'plan.rejected': '计划拒绝',
    'integration.started': '开始集成',
    'integration.completed': '集成完成',
    'integration.conflict': '集成冲突',
    'verify.started': '开始验证',
    'verify.passed': '验证通过',
    'verify.failed': '验证失败',
  };
  return labels[type] || type;
}

function formatEventPayload(payload: Record<string, unknown>): string {
  const taskId = payload.task_id;
  const artifactId = payload.artifact_id || payload.report_artifact_id;
  const reason = payload.reason;
  const status = payload.to_status;
  const parts = [
    typeof taskId === 'string' ? `任务 ${taskId}` : '',
    typeof status === 'string' ? `→ ${runStatusLabel(status as RunStatus)}` : '',
    typeof artifactId === 'string' ? `产物 ${artifactId}` : '',
    typeof reason === 'string' ? `原因 ${reason}` : '',
  ].filter(Boolean);
  return parts.length ? parts.join('，') : '';
}
