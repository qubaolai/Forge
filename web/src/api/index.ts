import { apiClient } from './client';
import {
  Agent, AuthTokens, ChatMessage, ChatSession, KnowledgeBase,
  KnowledgeDocument, DocumentChunk, LoginPayload, LoginResponse,
  ModelEndpoint, ModelUpsert, PaginatedData, PaginationParams,
  ProviderAdmin, ProviderKey, ProviderModel, RetrievalResult,
  RetrieveRequest, ToolDefinition, User, AuditLog,
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

// 仅保留 list（agent 编辑器的 ModelTab / PreviewPanel 仍在用）。
// 管理端的模型/供应商配置改用下方 providersApi / modelsAdminApi / providerKeysApi。
export const modelsApi = {
  list: () => apiClient.get<ModelEndpoint[]>('/models'),
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
  setDefault: (modelId: string) =>
    apiClient.put<ProviderModel>(`/models/${modelId}`, { is_default: true }),
  remove: (modelId: string) => apiClient.delete<void>(`/models/${modelId}`),
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

export interface ModelInfo {
  provider: string;
  model_id: string;
  name: string;
  display_name: string;
  model_type: string;
  context_window: number;
  supports_tools: boolean;
  supports_images: boolean;
  supports_thinking: boolean;
  thinking: { options?: string[] | null; default?: string | null } | null;
}

export interface ModelGroup {
  provider: string;
  models: ModelInfo[];
}

export interface GroupedModelsResponse {
  groups: ModelGroup[];
  providers?: string[];
  models?: ModelInfo[];
  provider?: string;
  model_type?: string;
}

export const systemApi = {
  models: (params?: { provider?: string; model_type?: string }) =>
    apiClient.get<GroupedModelsResponse>('/models', { params }),
};
