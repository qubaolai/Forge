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

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        originalRequest.headers.set('Authorization', `Bearer ${newToken}`);
        return axiosInstance(originalRequest);
      }
      window.location.href = '/login';
      return Promise.reject(new ApiError(401, '登录已过期,请重新登录'));
    }

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
