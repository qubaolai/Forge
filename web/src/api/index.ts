import { apiClient } from './client';
import {
  Agent, AuthTokens, ChatMessage, ChatSession, KnowledgeBase,
  KnowledgeDocument, DocumentChunk, LoginPayload, LoginResponse,
  GroupedModelsResponse, ModelChain, ModelChainEntry, ModelChainScope, ModelType, ModelUpsert,
  PaginatedData, PaginationParams, ProviderAdmin, ProviderKey, ProviderModel,
  RagIndexJob, RagIndexStatus, RetrievalResult, RetrieveRequest,
  SystemModelBinding, ToolDefinition, User, AuditLog,
} from '@/types';

export const authApi = {
  login: (payload: LoginPayload) => apiClient.post<LoginResponse>('/auth/login', payload),
  logout: () => apiClient.post<void>('/auth/logout'),
  me: () => apiClient.get<User>('/auth/me'),
  refresh: () => apiClient.post<AuthTokens>('/auth/refresh'),
  changePassword: (oldPwd: string, newPwd: string) =>
    apiClient.post<void>('/auth/change-password', { old_password: oldPwd, new_password: newPwd }),
};

export const usersApi = {
  list: (params: PaginationParams) => apiClient.get<PaginatedData<User>>('/users', { params }),
  create: (payload: Omit<User, 'id' | 'created_at' | 'updated_at'> & { password: string }) =>
    apiClient.post<User>('/users', payload),
  update: (id: string, payload: Partial<User>) => apiClient.patch<User>(`/users/${id}`, payload),
  remove: (id: string) => apiClient.delete<void>(`/users/${id}`),
  resetPassword: (id: string) => apiClient.post<{ temp_password: string }>(`/users/${id}/reset-password`),
};

export const sessionsApi = {
  list: (params: PaginationParams) => apiClient.get<PaginatedData<ChatSession>>('/sessions', { params }),
  create: (title?: string) => apiClient.post<ChatSession>('/sessions', { title }),
  get: (id: string) => apiClient.get<ChatSession>(`/sessions/${id}`),
  update: (id: string, payload: { title?: string }) => apiClient.patch<ChatSession>(`/sessions/${id}`, payload),
  remove: (id: string) => apiClient.delete<void>(`/sessions/${id}`),
  messages: (id: string, params: PaginationParams) =>
    apiClient.get<PaginatedData<ChatMessage>>(`/sessions/${id}/messages`, { params }),
};

export const chatApi = {
  stop: (messageId: string) => apiClient.post<void>('/chat/stop', { message_id: messageId }),
  resume: (messageId: string) =>
    apiClient.post<{ message_id: string }>('/chat/resume', { message_id: messageId }),
  regenerate: (messageId: string) =>
    apiClient.post<{ new_message_id: string }>('/chat/regenerate', { message_id: messageId }),
};

export const kbApi = {
  list: (params: PaginationParams) => apiClient.get<PaginatedData<KnowledgeBase>>('/knowledge-bases', { params }),
  create: (payload: Partial<KnowledgeBase>) => apiClient.post<KnowledgeBase>('/knowledge-bases', payload),
  get: (id: string) => apiClient.get<KnowledgeBase>(`/knowledge-bases/${id}`),
  update: (id: string, payload: Partial<KnowledgeBase>) =>
    apiClient.patch<KnowledgeBase>(`/knowledge-bases/${id}`, payload),
  remove: (id: string) => apiClient.delete<void>(`/knowledge-bases/${id}`),
  retrieve: (id: string, payload: RetrieveRequest) =>
    apiClient.post<RetrievalResult[]>(`/knowledge-bases/${id}/retrieve`, payload),
};

export const documentsApi = {
  list: (kbId: string, params: PaginationParams & { status?: string }) =>
    apiClient.get<PaginatedData<KnowledgeDocument>>(`/knowledge-bases/${kbId}/documents`, { params }),
  upload: (kbId: string, file: File, onProgress?: (p: number) => void) => {
    const form = new FormData();
    form.append('file', file);
    return apiClient.post<KnowledgeDocument>(
      `/knowledge-bases/${kbId}/documents/upload`,
      form,
      {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (e) => {
          if (e.total && onProgress) onProgress(Math.round((e.loaded * 100) / e.total));
        },
      },
    );
  },
  fromUrl: (kbId: string, url: string) =>
    apiClient.post<KnowledgeDocument>(`/knowledge-bases/${kbId}/documents/url`, { url }),
  get: (kbId: string, docId: string) =>
    apiClient.get<KnowledgeDocument>(`/knowledge-bases/${kbId}/documents/${docId}`),
  chunks: (kbId: string, docId: string, params: PaginationParams) =>
    apiClient.get<PaginatedData<DocumentChunk>>(`/knowledge-bases/${kbId}/documents/${docId}/chunks`, { params }),
  reindex: (kbId: string, docId: string) =>
    apiClient.post<void>(`/knowledge-bases/${kbId}/documents/${docId}/reindex`),
  remove: (kbId: string, docId: string) =>
    apiClient.delete<void>(`/knowledge-bases/${kbId}/documents/${docId}`),
};

export const agentsApi = {
  list: (params: PaginationParams) => apiClient.get<PaginatedData<Agent>>('/agents', { params }),
  create: (payload: Partial<Agent>) => apiClient.post<Agent>('/agents', payload),
  get: (id: string) => apiClient.get<Agent>(`/agents/${id}`),
  update: (id: string, payload: Partial<Agent>) => apiClient.patch<Agent>(`/agents/${id}`, payload),
  remove: (id: string) => apiClient.delete<void>(`/agents/${id}`),
  duplicate: (id: string) => apiClient.post<Agent>(`/agents/${id}/duplicate`),
};

export const toolsApi = {
  list: () => apiClient.get<ToolDefinition[]>('/tools'),
};

// Agent 编辑器只允许选择 Chat 模型，返回扁平列表便于消费。
export const modelsApi = {
  list: async () => {
    const response = await apiClient.get<GroupedModelsResponse>('/models', {
      params: { model_type: 'chat' },
    });
    return response.models || response.groups.flatMap((group) => group.models);
  },
};

// ---- 管理端：供应商（不支持新增供应商，仅启停 + 读取） ----
export const providersApi = {
  listAdmin: () => apiClient.get<ProviderAdmin[]>('/providers/admin'),
  toggle: (name: string, enabled: boolean) =>
    apiClient.put<{ provider: string; enabled: boolean }>(`/providers/${name}`, { enabled }),
};

// ---- 管理端：模型增删改查 ----
export const modelsAdminApi = {
  detail: (id: string) =>
    apiClient.get<ProviderModel>(`/models/${id}`),
  create: (provider: string, payload: ModelUpsert) =>
    apiClient.post<ProviderModel>(`/providers/${provider}/models`, payload),
  update: (modelId: string, payload: ModelUpsert) =>
    apiClient.put<ProviderModel>(`/models/${modelId}`, payload),
  toggle: (modelId: string, enabled: boolean) =>
    apiClient.put<ProviderModel>(`/models/${modelId}`, { enabled }),
  remove: (modelId: string) => apiClient.delete<void>(`/models/${modelId}`),
};

export const modelBindingsApi = {
  list: () => apiClient.get<SystemModelBinding[]>('/admin/model-bindings'),
  update: (role: string, modelId: string | null) =>
    apiClient.put<Pick<SystemModelBinding, 'role' | 'model_id' | 'version'>>(
      `/admin/model-bindings/${role}`,
      { model_id: modelId },
    ),
};

// ---- 管理端:模型调用链(对话链 / 档位链)----
export const modelChainsApi = {
  list: (scope: ModelChainScope) =>
    apiClient.get<ModelChain[]>('/admin/model-chains', { params: { scope } }),
  update: (scope: ModelChainScope, chainKey: string, entries: ModelChainEntry[]) =>
    apiClient.put<ModelChain>(`/admin/model-chains/${scope}/${chainKey}`, { entries }),
};

export const ragIndexAdminApi = {
  status: () => apiClient.get<RagIndexStatus>('/admin/rag-index/status'),
  rebuild: () => apiClient.post<RagIndexJob>('/admin/rag-index/rebuild'),
  retry: (jobId: string) =>
    apiClient.post<RagIndexJob>(`/admin/rag-index/rebuild/${jobId}/retry`),
};

// ---- 管理端：供应商 API-Key 增删改查 ----
export const providerKeysApi = {
  list: (provider: string) => apiClient.get<ProviderKey[]>(`/providers/${provider}/keys`),
  create: (provider: string, apiKey: string, weight = 1) =>
    apiClient.post<ProviderKey>(`/providers/${provider}/keys`, { api_key: apiKey, weight }),
  update: (provider: string, keyId: string, payload: { enabled?: boolean; weight?: number }) =>
    apiClient.put<ProviderKey>(`/providers/${provider}/keys/${keyId}`, payload),
  remove: (provider: string, keyId: string) =>
    apiClient.delete<void>(`/providers/${provider}/keys/${keyId}`),
};

export const auditApi = {
  list: (params: PaginationParams & { user_id?: string; action?: string }) =>
    apiClient.get<PaginatedData<AuditLog>>('/audit-logs', { params }),
};

export const systemApi = {
  models: (params?: { provider?: string; model_type?: ModelType }) =>
    apiClient.get<GroupedModelsResponse>('/models', { params }),
};
