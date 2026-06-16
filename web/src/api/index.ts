import { apiClient } from './client';
import { useAuthStore } from '@/store/auth';
import {
  AuthTokens, ChatFilePreview, ChatMessage, ChatSession, LoginPayload, LoginResponse,
  GroupedModelsResponse, ModelChain, ModelChainEntry, ModelChainScope, ModelType, ModelUpsert,
  PaginatedData, PaginationParams, ProviderAdmin, ProviderKey, ProviderModel,
  RagIndexJob, RagIndexStatus,
  SystemModelBinding, User,
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

export const systemApi = {
  models: (params?: { provider?: string; model_type?: ModelType }) =>
    apiClient.get<GroupedModelsResponse>('/models', { params }),
};

// ---- 会话文件：附件上传 / 预览 / 下载 ----
export const filesApi = {
  /** 上传会话附件 (超阈值大段输入 / 文件), 返回文件元数据 */
  uploadAttachment: (sessionId: string, file: File) => {
    const form = new FormData();
    form.append('session_id', sessionId);
    form.append('file', file);
    return apiClient.post<{ id: string; name: string; size_bytes: number; mime_type?: string | null }>(
      '/chat/attachments',
      form,
    );
  },
  /** 预览文件文本内容 (可按行区间切片) */
  previewContent: (fileId: string, range?: { start: number; end: number }) =>
    apiClient.get<ChatFilePreview>(`/files/${fileId}/content`, {
      params: range ? { start: range.start, end: range.end } : undefined,
    }),
  /** 带鉴权下载单文件 */
  download: (fileId: string, filename: string) =>
    authedDownload(`/files/${fileId}/download`, filename),
  /** 打包下载某条消息生成的全部文件 (zip) */
  downloadArchive: (messageId: string) =>
    authedDownload(`/files/message/${messageId}/archive`, `files-${messageId}.zip`),
};

/** 带鉴权 fetch blob 并触发浏览器保存 (单文件 / zip 通用) */
async function authedDownload(path: string, filename: string) {
  const token = useAuthStore.getState().accessToken;
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1';
  const resp = await fetch(`${base}${path}`, {
    headers: {
      'X-Client-Type': 'web',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    credentials: 'include',
  });
  if (!resp.ok) throw new Error('下载失败');
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
