import { apiClient } from './client';
import type {
  KnowledgeBase,
  KnowledgeChunkFullText,
  KnowledgeDocument,
  KbDocumentChunkListData,
  KbRetrievalTrace,
  KbSearchHit,
  Visibility,
} from '@/types';

export interface KbListData {
  items: KnowledgeBase[];
  total: number;
}

export interface KbDocumentListData {
  items: KnowledgeDocument[];
  total: number;
  page: number;
  page_size: number;
}

export interface KbSearchData {
  items: KbSearchHit[];
  total: number;
  query: string;
  trace?: KbRetrievalTrace | null;
}

export interface KbCreatePayload {
  name: string;
  description?: string;
  visibility: Visibility;
  chunk_size?: number;
  chunk_overlap?: number;
}

export interface KbUpdatePayload {
  name?: string;
  description?: string | null;
  visibility?: Visibility;
  chunk_size?: number;
  chunk_overlap?: number;
}

/** 知识库 (KB) 管理 API。路径对齐后端 /api/v1/kb。 */
export const kbApi = {
  list: () => apiClient.get<KbListData>('/kb'),
  create: (payload: KbCreatePayload) => apiClient.post<KnowledgeBase>('/kb', payload),
  get: (id: string) => apiClient.get<KnowledgeBase>(`/kb/${id}`),
  update: (id: string, payload: KbUpdatePayload) =>
    apiClient.put<KnowledgeBase>(`/kb/${id}`, payload),
  remove: (id: string) => apiClient.delete<void>(`/kb/${id}`),

  // ---- 文档 ----
  listDocuments: (
    kbId: string,
    params?: { status?: string; page?: number; page_size?: number },
  ) => apiClient.get<KbDocumentListData>(`/kb/${kbId}/documents`, { params }),
  getDocument: (kbId: string, docId: string) =>
    apiClient.get<KnowledgeDocument>(`/kb/${kbId}/documents/${docId}`),
  listDocumentChunks: (
    kbId: string,
    docId: string,
    params?: { page?: number; page_size?: number },
  ) => apiClient.get<KbDocumentChunkListData>(`/kb/${kbId}/documents/${docId}/chunks`, { params }),
  getChunkFullText: (chunkId: string) =>
    apiClient.get<KnowledgeChunkFullText>(`/kb/chunks/${encodeURIComponent(chunkId)}`),
  uploadDocument: (kbId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return apiClient.post<{ id: string; name: string; status: string }>(
      `/kb/${kbId}/documents`,
      form,
    );
  },
  removeDocument: (kbId: string, docId: string) =>
    apiClient.delete<void>(`/kb/${kbId}/documents/${docId}`),
  rebuildDocument: (kbId: string, docId: string) =>
    apiClient.post<{ id: string; vector_index_status: string }>(
      `/kb/${kbId}/documents/${docId}/rebuild`,
    ),
  reingestDocument: (kbId: string, docId: string) =>
    apiClient.post<{ id: string; status: string }>(
      `/kb/${kbId}/documents/${docId}/reingest`,
    ),

  // ---- 检索测试 ----
  search: (kbId: string, payload: { query: string; top_n?: number; debug?: boolean }) =>
    apiClient.post<KbSearchData>(`/kb/${kbId}/search`, payload),
};
