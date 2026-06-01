import axios, { AxiosError, AxiosInstance, AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios';
import { ApiError, ApiResponse } from '@/types';
import { useAuthStore } from '@/store/auth';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

const axiosInstance: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  withCredentials: true,
});

// ---- 请求拦截器:注入 access token ----
axiosInstance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  config.headers.set('X-Client-Type', 'web');
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

// ---- 401 自动刷新 ----
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const resp = await axios.post<ApiResponse<{ access_token: string; expires_at: string }>>(
        `${BASE_URL}/auth/refresh`,
        {},
        { withCredentials: true, headers: { 'X-Client-Type': 'web' } },
      );
      if (resp.data.code !== 0) throw new Error(resp.data.message);
      const token = resp.data.data.access_token;
      useAuthStore.getState().setAccessToken(token, resp.data.data.expires_at);
      return token;
    } catch {
      useAuthStore.getState().clear();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

// ---- 响应拦截器:统一错误处理 ----
axiosInstance.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResponse;
    if (body.code !== 0) {
      throw new ApiError(body.code, body.message, body.details, response.status);
    }
    return response;
  },
  async (error: AxiosError<ApiResponse>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };
    const url = originalRequest?.url || '';
    // 登录 / 刷新接口自身的 401 是"凭证错误"，不是"会话过期"：
    // 不要去刷新、更不要整页跳转，直接抛给调用方显示错误（如登录页的错误提示）。
    const isAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/refresh');

    if (error.response?.status === 401 && !originalRequest._retry && !isAuthEndpoint) {
      originalRequest._retry = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        originalRequest.headers.set('Authorization', `Bearer ${newToken}`);
        return axiosInstance(originalRequest);
      }
      // 刷新失败 = 会话确实过期：清理认证态，仅在不在登录页时才跳转，
      // 避免在登录页反复整页刷新、冲掉表单与错误提示。
      useAuthStore.getState().clear();
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
      return Promise.reject(new ApiError(401, '登录已过期,请重新登录'));
    }

    // 其余非成功响应（业务错误码 / 4xx / 5xx / 网络错误）一律只抛 ApiError，
    // 交给调用方（mutation.onError / try-catch）处理，绝不刷新页面。
    const body = error.response?.data;
    if (body && typeof body === 'object' && 'code' in body) {
      throw new ApiError(body.code, body.message, body.details, error.response?.status);
    }
    throw new ApiError(-1, error.message || '网络错误');
  },
);

/**
 * 业务 API 客户端:每个方法都返回 Promise<T>(业务数据本身)。
 */
export const apiClient = {
  async get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const resp = await axiosInstance.get<ApiResponse<T>>(url, config);
    return resp.data.data;
  },
  async post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await axiosInstance.post<ApiResponse<T>>(url, data, config);
    return resp.data.data;
  },
  async put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await axiosInstance.put<ApiResponse<T>>(url, data, config);
    return resp.data.data;
  },
  async patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await axiosInstance.patch<ApiResponse<T>>(url, data, config);
    return resp.data.data;
  },
  async delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const resp = await axiosInstance.delete<ApiResponse<T>>(url, config);
    return resp.data.data;
  },
};

export { axiosInstance };
