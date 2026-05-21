import { Agent } from '@/types';

/**
 * 编辑器使用的"草稿态":只保留可编辑字段。
 * 服务端字段(id/owner_id/created_at 等)不出现在草稿里,提交时才组装。
 */
export type AgentDraft = Pick<
  Agent,
  | 'name'
  | 'description'
  | 'visibility'
  | 'system_prompt'
  | 'opening_message'
  | 'model'
  | 'retrieval'
  | 'tools'
  | 'advanced'
>;

export function createDefaultDraft(): AgentDraft {
  return {
    name: '',
    description: '',
    visibility: 'private',
    system_prompt: '你是一个智能助手,基于提供的知识库内容回答问题。\n\n回答时:\n1. 仅使用知识库内容,不臆造信息\n2. 标注引用来源,使用 [1] [2] 格式\n3. 知识库无相关内容时直接说明',
    opening_message: '你好!我能帮你解答什么问题?',
    model: {
      model_id: '',
      temperature: 0.3,
      max_tokens: 2048,
      top_p: 0.9,
    },
    retrieval: {
      kb_ids: [],
      top_k: 4,
      score_threshold: 0.5,
      hybrid: true,
      rerank: false,
    },
    tools: [],
    advanced: {
      context_window: 8192,
      timeout_seconds: 60,
      max_retries: 1,
      enable_streaming: true,
    },
  };
}

export function agentToDraft(agent: Agent): AgentDraft {
  return {
    name: agent.name,
    description: agent.description,
    visibility: agent.visibility,
    system_prompt: agent.system_prompt,
    opening_message: agent.opening_message,
    model: agent.model,
    retrieval: agent.retrieval,
    tools: agent.tools,
    advanced: agent.advanced,
  };
}
