import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { sessionsApi, agentsApi, systemApi, ModelInfo } from '@/api';
import { ChatMessage, Citation } from '@/types';
import { useChatStream } from '@/hooks/useChatStream';
import { MessageList } from '@/components/chat/MessageList';
import { ChatInput, ReasoningEffort } from '@/components/chat/ChatInput';
import { CitationPanel } from '@/components/chat/CitationPanel';
import { PanelRight } from 'lucide-react';
import type { ModelOptions } from '@/hooks/useChatStream';

export default function ChatPage() {
  const { sessionId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const isNew = sessionId === 'new';
  const noSession = !sessionId; // /chat 根路由
  const agentIdParam = searchParams.get('agent');

  const skipResetRef = useRef(false);

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

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list({ page: 1, page_size: 50 }),
  });

  const [pendingUser, setPendingUser] = useState<ChatMessage[]>([]);
  const [showPanel, setShowPanel] = useState(true);
  const [selectedCitation, setSelectedCitation] = useState<number | null>(null);
  const [reasoning, setReasoning] = useState<ReasoningEffort>('high');
  const [thinkingEnabled, setThinkingEnabled] = useState(true);

  // 模型选择
  const [selectedProvider, setSelectedProvider] = useState('anthropic');
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-6');
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);

  // 加载可用模型列表
  const loadModelsForProvider = useCallback(async (provider: string) => {
    try {
      const res = await systemApi.models(provider);
      const models = res.models || [];
      setAvailableModels(models);
      if (models.length > 0) {
        setSelectedModel(models[0].name);
      }
    } catch {
      setAvailableModels([]);
    }
  }, []);

  useEffect(() => {
    loadModelsForProvider(selectedProvider);
  }, [selectedProvider, loadModelsForProvider]);

  const { send, abort, resume, reset, streaming, current } = useChatStream({
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
        agent_id: agents?.items?.[0]?.id || 'default',
        agent_name: agents?.items?.[0]?.name || '',
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
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // 统一的消息列表：history + pendingUser + current（去重）
  const messages = useMemo<ChatMessage[]>(() => {
    const historyItems = history?.items || [];
    const historyIds = new Set(historyItems.map((m) => m.id));
    const list: ChatMessage[] = [...historyItems];

    for (const m of pendingUser) {
      const confirmedByHistory = historyItems.some(
        (h) =>
          h.role === 'user' &&
          h.content === m.content &&
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

  function buildModelOptions(): ModelOptions {
    const opts: ModelOptions = { provider: selectedProvider, model: selectedModel };
    const meta = availableModels.find((m) => m.name === selectedModel)?.thinking;
    if (meta?.type === 'reasoning_effort') {
      opts.reasoning_effort = reasoning;
    } else if (meta?.type === 'enabled' && thinkingEnabled) {
      opts.thinking = true;
      opts.thinking_budget = 5000;
    }
    return opts;
  }

  function handleSend(text: string) {
    if (noSession) return;

    const pendingMsg: ChatMessage = {
      id: 'tmp_user_' + Date.now(),
      session_id: isNew ? '' : sessionId!,
      role: 'user',
      content: text,
      status: 'done',
      created_at: new Date().toISOString(),
    };
    setPendingUser((prev) => [...prev, pendingMsg]);

    const modelOptions = buildModelOptions();
    if (isNew) {
      const agentId = agentIdParam || agents?.items?.[0]?.id;
      send(null, text, agentId, undefined, modelOptions);
    } else {
      send(sessionId!, text, undefined, undefined, modelOptions);
    }
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
          {!isNew && !showPanel && currentCitations.length > 0 && (
            <button
              onClick={() => setShowPanel(true)}
              className="text-xs text-gray-500 hover:text-gray-900 flex items-center gap-1"
            >
              <PanelRight size={14} />
              引用 {currentCitations.length}
            </button>
          )}
        </div>

        {showLoading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
            加载中…
          </div>
        ) : showEmptyHint ? (
          <div className="flex-1 flex items-center justify-center text-gray-300 text-sm select-none">
            输入消息开始对话
          </div>
        ) : (
          <MessageList
            key={sessionId || 'new'}
            messages={messages}
            onCitationClick={handleCitationClick}
            onResume={handleResume}
          />
        )}

        <ChatInput
          onSend={handleSend}
          onAbort={abort}
          streaming={streaming}
          disabled={false}
          placeholder={undefined}
          reasoning={reasoning}
          onReasoningChange={setReasoning}
          selectedProvider={selectedProvider}
          selectedModel={selectedModel}
          availableModels={availableModels}
          onProviderChange={setSelectedProvider}
          onModelChange={setSelectedModel}
          thinkingEnabled={thinkingEnabled}
          onThinkingChange={setThinkingEnabled}
        />
      </main>

      {!isNew && showPanel && currentCitations.length > 0 && (
        <CitationPanel
          citations={currentCitations}
          highlightedIndex={selectedCitation}
          onClose={() => setShowPanel(false)}
        />
      )}
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
