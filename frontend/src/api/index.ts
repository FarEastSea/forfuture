import axios, { AxiosHeaders } from 'axios'

import { buildAdminAuthHeaders, getStoredAdminToken } from '@/utils/adminToken'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

api.interceptors.request.use((config) => {
  const token = getStoredAdminToken()
  if (!token) {
    return config
  }

  const headers = AxiosHeaders.from(config.headers)
  headers.set('Authorization', `Bearer ${token}`)
  config.headers = headers

  return config
})

// QQ
export const qqApi = {
  getPosts: (params?: any) => api.get('/qq/posts', { params }),
  getPost: (id: number) => api.get(`/qq/posts/${id}`),
  crawl: (accountIds: string[], mode: string = 'incremental') => api.post('/qq/crawl', { account_ids: accountIds, mode }),
  getCrawlStatus: () => api.get('/qq/crawl/status'),
  getAccounts: () => api.get('/qq/accounts'),
}

// 小红书
export const xhsApi = {
  getNotes: (params?: any) => api.get('/xhs/notes', { params }),
  getNote: (id: number) => api.get(`/xhs/notes/${id}`),
  crawl: (userIds: string[], mode: string = 'incremental') => api.post('/xhs/crawl', { user_ids: userIds, mode }),
  crawlNoteComments: (noteDbId: number) => api.post(`/xhs/notes/${noteDbId}/crawl-comments`),
  getCrawlStatus: () => api.get('/xhs/crawl/status'),
  getAccounts: () => api.get('/xhs/accounts'),
}

// 登录
export const authApi = {
  addAccount: (data: any) => api.post('/auth/accounts', data),
  getAccounts: (platform?: string) => api.get('/auth/accounts', { params: { platform } }),
  deleteAccount: (id: number) => api.delete(`/auth/accounts/${id}`),
  toggleAccount: (id: number) => api.post(`/auth/accounts/${id}/toggle`),
  refreshAccount: (id: number) => api.post(`/auth/accounts/${id}/refresh`),
  refreshAllAccounts: () => api.post('/auth/accounts/refresh-all'),
  getQQQrcode: () => api.get('/auth/qq/qrcode'),
  getQQStatus: () => api.get('/auth/qq/status'),
  getQQDebugView: () => api.get('/auth/qq/debug-view'),
  refreshQQCookies: () => api.post('/auth/qq/refresh-cookies'),
  qqNapcatLogin: () => api.post('/auth/qq/napcat-login'),
  getXHSQrcode: () => api.get('/auth/xhs/qrcode'),
  getXHSStatus: () => api.get('/auth/xhs/status'),
  getXHSDebugView: () => api.get('/auth/xhs/debug-view'),
  xhsNavigate: (url: string) => api.post('/auth/xhs/navigate', { url }),
  xhsSaveCookies: () => api.post('/auth/xhs/save-cookies'),
  xhsResetBrowser: () => api.post('/auth/xhs/reset-browser'),
}

// AI对话
export const chatApi = {
  getSessions: () => api.get('/chat/sessions'),
  createSession: () => api.post('/chat/sessions'),
  deleteSession: (id: number) => api.delete(`/chat/sessions/${id}`),
  getMessages: (sessionId: number) => api.get(`/chat/sessions/${sessionId}/messages`),
  sendMessage: (sessionId: number, content: string) => {
    return fetch(`/api/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: buildAdminAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ content }),
    })
  },
}

// AI配置
export const aiConfigApi = {
  getConfigs: () => api.get('/ai-config'),
  createConfig: (data: any) => api.post('/ai-config', data),
  updateConfig: (id: number, data: any) => api.put(`/ai-config/${id}`, data),
  deleteConfig: (id: number) => api.delete(`/ai-config/${id}`),
  activateConfig: (id: number) => api.post(`/ai-config/${id}/activate`),
  testConfig: (data: any) => api.post('/ai-config/test', data),
}

// 定时任务
export const taskApi = {
  getTasks: () => api.get('/tasks'),
  createTask: (data: any) => api.post('/tasks', data),
  updateTask: (id: number, data: any) => api.put(`/tasks/${id}`, data),
  deleteTask: (id: number) => api.delete(`/tasks/${id}`),
  toggleTask: (id: number) => api.post(`/tasks/${id}/toggle`),
  runNow: (id: number) => api.post(`/tasks/${id}/run-now`),
  getLogs: (id: number) => api.get(`/tasks/${id}/logs`),
}

// 系统配置
export const systemApi = {
  getConfigs: () => api.get('/system/configs'),
  updateConfigs: (items: any[]) => api.put('/system/configs', items),
  getDbConfig: () => api.get('/system/db-config'),
  testDbConnection: (data: any) => api.post('/system/db-config/test', data),
  getNapcatStatus: () => api.get('/system/napcat-status'),
  getNapcatLogs: () => api.get('/system/napcat-logs'),
  getLogs: (params?: { level?: string; module?: string; keyword?: string; limit?: number }) =>
    api.get('/system/logs', { params }),
  clearLogs: () => api.delete('/system/logs'),
  // 动态总结
  getSummaryStats: () => api.get('/system/summary/stats'),
  generateSummaries: (forceAll: boolean = false) => api.post('/system/summary/generate', null, { params: { force_all: forceAll } }),
}

export default api
