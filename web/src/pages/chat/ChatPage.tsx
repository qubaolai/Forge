import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { filesApi, sessionsApi, systemApi } from '@/api';
import { ChatMessage, ChatFileMeta, Citation, ModelGroup } from '@/types';
import { useChatStream } from '@/hooks/useChatStream';
import { MessageList } from '@/components/chat/MessageList';
import { ChatInput, ThinkingLevel } from '@/components/chat/ChatInput';
import { CitationPanel } from '@/components/chat/CitationPanel';
import { FilePreviewPanel } from '@/components/chat/FilePreviewPanel';
import { ContextUsageRing } from '@/components/chat/ContextUsageRing';
import { ChatWelcome } from '@/components/chat/ChatWelcome';
import { PanelRight } from 'lucide-react';
import type { ModelOptions } from '@/hooks/useChatStream';
import type { ContextUsage } from '@/types';

export default function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const isNew = sessionId === 'new';
  const noSession = !sessionId; // /chat 根路由

  const skipResetRef = useRef(false);
  // 新会话上传附件时先建会话, 把真实 id 暂存在此 (供 handleSend 复用, 避免依赖异步 state)
  const ensuredSessionIdRef = useRef<string | null>(null);

  const { data: history, isLoading } = useQuery({
    queryKey: ['session-messages', sessionId],
    queryFn: () => sessionsApi.messages(sessionId!, { page: 1, page_size: 100 }),
    enabled: !!sessionId && !isNew,
  });

  const { data: session } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => sessionsApi.get(sessionId!),
    enabled: !!sessionId && !isNew,
  });

  const [pendingUser, setPendingUser] = useState<ChatMessage[]>([]);
  const [showPanel, setShowPanel] = useState(true);
  const [selectedCitation, setSelectedCitation] = useState<number | null>(null);
  const [previewFile, setPreviewFile] = useState<ChatFileMeta | null>(null);
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('medium');
  const [thinkingEnabled, setThinkingEnabled] = useState(true);
  const [prefill, setPrefill] = useState('');

  // 模型选择
  const [selectedProvider, setSelectedProvider] = useState('anthropic');
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-6');
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);

  // 加载分组模型列表（按供应商）
  const loadModelGroups = useCallback(async () => {
    try {
      const res = await systemApi.models({ model_type: 'chat' });
      const groups = res.groups || [];
      setModelGroups(groups);

      // 保持当前选择；若当前模型已不存在则回退到第一项
      const hasSelected = groups.some(
        (group) =>
          group.provider === selectedProvider &&
          group.models.some((model) => model.name === selectedModel),
      );
      if (hasSelected) return;

      const firstGroup = groups.find((group) => group.models.length > 0);
      if (firstGroup && firstGroup.models[0]) {
        setSelectedProvider(firstGroup.provider);
        setSelectedModel(firstGroup.models[0].name);
      }
    } catch {
      setModelGroups([]);
    }
  }, [selectedProvider, selectedModel]);

  useEffect(() => {
    loadModelGroups();
  }, [loadModelGroups]);

  const currentModelMeta = useMemo(() => {
    for (const group of modelGroups) {
      if (group.provider !== selectedProvider) continue;
      const matched = group.models.find((m) => m.name === selectedModel);
      if (matched) return matched;
    }
    return null;
  }, [modelGroups, selectedProvider, selectedModel]);

  const { send, abort, resume, reset, streaming, current, contextUsage } = useChatStream({
    onComplete: () => {
      qc.invalidateQueries({ queryKey: ['session-messages', sessionId] });
      qc.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: ['session-messages', sessionId] });
    },
    onSessionCreated: (newSessionId, title) => {
      // 流式继续中导航到真实 URL：用 ref 标记跳过 Effect 清场
      skipResetRef.current = true;
      qc.setQueryData(['session', newSessionId], {
        id: newSessionId,
        title,
        agent_id: '',
        agent_name: '',
        user_id: '',
        message_count: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      navigate(`/chat/${newSessionId}`, { replace: true });
      qc.invalidateQueries({ queryKey: ['sessions'] });
    },
    onSessionRenamed: (_sid, title) => {
      qc.setQueryData(['session', sessionId], (old: typeof session) =>
        old ? { ...old, title } : old,
      );
      qc.invalidateQueries({ queryKey: ['sessions'] });
    },
  });

  // Effect 1：sessionId 切换时清场（新会话导航跳过）
  useEffect(() => {
    if (skipResetRef.current) {
      skipResetRef.current = false;
      return;
    }
    setPendingUser([]);
    setSelectedCitation(null);
    setShowPanel(true);
    setPrefill('');
    setPreviewFile(null);
    ensuredSessionIdRef.current = null;
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // 统一的消息列表：history + pendingUser + current（去重）
  const messages = useMemo<ChatMessage[]>(() => {
    const historyItems = history?.items || [];
    const historyIds = new Set(historyItems.map((m) => m.id));
    const list: ChatMessage[] = [...historyItems];

    // 归一化: 剥离后端给 user 消息追加的 [file:<id>] 附件占位, 避免 pending(纯文本) 与
    // history(带占位) content 不一致导致去重失败、消息重复/顺序错乱。
    const normContent = (s: string) => s.replace(/\n*\[file:[^\]]*\]/g, '').trim();
    for (const m of pendingUser) {
      const confirmedByHistory = historyItems.some(
        (h) =>
          h.role === 'user' &&
          normContent(h.content) === normContent(m.content) &&
          messageTime(h) >= messageTime(m) - 60_000,
      );
      if (!historyIds.has(m.id) && !confirmedByHistory) list.push(m);
    }

    if (current) {
      const histIdx = list.findIndex((m) => m.id === current.id);
      if (histIdx >= 0) {
        list[histIdx] = { ...current, created_at: list[histIdx].created_at };
      } else {
        list.push(current);
      }
    }

    return list
      .map((message, index) => ({ message, index }))
      .sort((a, b) => {
        const byTime = messageTime(a.message) - messageTime(b.message);
        if (byTime !== 0) return byTime;
        const byRole = roleOrder(a.message) - roleOrder(b.message);
        if (byRole !== 0) return byRole;
        return a.index - b.index;
      })
      .map(({ message }) => message);
  }, [history, pendingUser, current]);

  const currentCitations = useMemo<Citation[]>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && m.citations && m.citations.length > 0) {
        return m.citations;
      }
    }
    return [];
  }, [messages]);

  // 上下文占用: 实时 SSE 优先, 否则回退到最后一条带 context_usage 的 assistant 消息 (持久化展示)
  // context_window 强制使用前端当前选中模型的值, 确保占比与下拉框一致
  const displayUsage = useMemo<ContextUsage | null>(() => {
    const base = (() => {
      if (contextUsage) return contextUsage;
      for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i];
        if (m.role === 'assistant' && m.context_usage) return m.context_usage;
      }
      return null;
    })();
    if (!base) return null;
    const cw = currentModelMeta?.config?.context_window || base.context_window;
    if (cw === base.context_window) return base;
    return {
      ...base,
      context_window: cw,
      total_ratio: cw > 0 ? base.input_tokens / cw : 0,
      layers: base.layers.map((l) => ({
        ...l,
        ratio: cw > 0 ? l.token_count / cw : 0,
      })),
    };
  }, [contextUsage, messages, currentModelMeta]);

  function buildModelOptions(): ModelOptions {
    const opts: ModelOptions = { provider: selectedProvider, model: selectedModel };
    if (!currentModelMeta?.config?.capabilities?.includes('thinking')) {
      return opts;
    }
    opts.thinking = thinkingEnabled;
    const levels = (currentModelMeta.thinking?.options || []).filter(Boolean);
    if (thinkingEnabled && levels.length > 0) {
      const defaultLevel = currentModelMeta.thinking?.default || levels[0];
      const resolvedLevel = levels.includes(thinkingLevel) ? thinkingLevel : defaultLevel;
      opts.thinking_level = resolvedLevel as ThinkingLevel;
    }
    return opts;
  }

  function handleSend(text: string, attachments?: { file_id: string; type: string }[]) {
    if (noSession) return;

    // 优先用「上传附件时已确保的会话 id」, 否则按路由判断 (新会话传 null 让后端建)
    const targetSid = ensuredSessionIdRef.current ?? (isNew ? null : sessionId!);

    const pendingMsg: ChatMessage = {
      id: 'tmp_user_' + Date.now(),
      session_id: targetSid ?? '',
      role: 'user',
      content: text,
      status: 'done',
      created_at: new Date().toISOString(),
    };
    setPendingUser((prev) => [...prev, pendingMsg]);

    const modelOptions = buildModelOptions();
    send(targetSid, text, attachments, modelOptions);
  }

  /** 上传会话附件: 新会话先建会话拿真实 id (附件需归属会话沙盒) */
  async function handleUploadAttachment(file: File): Promise<{ id: string; name: string }> {
    let sid = ensuredSessionIdRef.current ?? (isNew || noSession ? null : sessionId ?? null);
    if (!sid) {
      const s = await sessionsApi.create();
      sid = s.id;
      ensuredSessionIdRef.current = sid;
      skipResetRef.current = true; // 导航到真实会话时跳过 Effect 清场
      qc.setQueryData(['session', sid], s);
      qc.invalidateQueries({ queryKey: ['sessions'] });
      navigate(`/chat/${sid}`, { replace: true });
    }
    const res = await filesApi.uploadAttachment(sid, file);
    return { id: res.id, name: res.name };
  }

  function handleFilePreview(file: ChatFileMeta) {
    setPreviewFile(file);
  }

  function handleCitationClick(c: Citation) {
    setShowPanel(true);
    setSelectedCitation(c.index);
  }

  function handleResume(messageId: string) {
    // 找到已有消息继续生成: 从 messages 列表或 history 中定位 partial/aborted 的 assistant 消息
    const msg = messages.find((m) => m.id === messageId);
    if (msg) {
      resume(msg);
    }
  }

  // /chat 根路由：选择会话提示
  if (noSession) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400 text-sm">
        从左侧选择会话或点击「新对话」开始
      </div>
    );
  }

  // 统一渲染：isNew 与已有会话共用同一棵 DOM 树，避免分支切换造成的重挂载
  const headerTitle = isNew ? '新对话' : session?.title || '加载中…';
  const showLoading = !isNew && isLoading && messages.length === 0;
  const showEmptyHint = isNew && messages.length === 0;

  return (
    <div className="flex h-full">
      <main className="flex-1 flex flex-col min-w-0">
        <div className="px-6 py-3 border-b flex items-center justify-between shrink-0">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm">
              <span className={isNew ? 'font-medium text-gray-400' : 'font-medium'}>
                {headerTitle}
              </span>
              {!isNew && session && (
                <>
                  <span className="text-gray-300">·</span>
                  <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full">
                    {session.agent_name}
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {!isNew && !showPanel && currentCitations.length > 0 && (
              <button
                onClick={() => setShowPanel(true)}
                className="text-xs text-gray-500 hover:text-gray-900 flex items-center gap-1"
              >
                <PanelRight size={14} />
                引用 {currentCitations.length}
              </button>
            )}
            <ContextUsageRing usage={displayUsage} />
          </div>
        </div>

        {showLoading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
            加载中…
          </div>
        ) : showEmptyHint ? (
          <ChatWelcome onPick={(t) => setPrefill(t)} />
        ) : (
          <MessageList
            key={sessionId || 'new'}
            messages={messages}
            onCitationClick={handleCitationClick}
            onResume={handleResume}
            onFilePreview={handleFilePreview}
          />
        )}

        <ChatInput
          onSend={handleSend}
          onUploadAttachment={handleUploadAttachment}
          onAbort={abort}
          streaming={streaming}
          disabled={false}
          placeholder={undefined}
          thinkingLevel={thinkingLevel}
          onThinkingLevelChange={setThinkingLevel}
          prefill={prefill}
          selectedProvider={selectedProvider}
          selectedModel={selectedModel}
          modelGroups={modelGroups}
          onModelChange={(provider, model) => {
            setSelectedProvider(provider);
            setSelectedModel(model);
          }}
          thinkingEnabled={thinkingEnabled}
          onThinkingChange={setThinkingEnabled}
        />
      </main>

      {previewFile ? (
        <FilePreviewPanel file={previewFile} onClose={() => setPreviewFile(null)} />
      ) : !isNew && showPanel && currentCitations.length > 0 ? (
        <CitationPanel
          citations={currentCitations}
          highlightedIndex={selectedCitation}
          onClose={() => setShowPanel(false)}
        />
      ) : null}
    </div>
  );
}

function messageTime(message: ChatMessage): number {
  const time = new Date(message.created_at).getTime();
  return Number.isFinite(time) ? time : 0;
}

function roleOrder(message: ChatMessage): number {
  return message.role === 'user' ? 0 : 1;
}
