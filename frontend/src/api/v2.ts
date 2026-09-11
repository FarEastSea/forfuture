import axios, { AxiosHeaders } from 'axios'

import { buildAdminAuthHeaders, getStoredAdminToken } from '@/utils/adminToken'

const v2 = axios.create({ baseURL: '/api/v2', timeout: 60000 })

v2.interceptors.request.use((config) => {
  const token = getStoredAdminToken()
  if (!token) {
    return config
  }
  const headers = AxiosHeaders.from(config.headers)
  headers.set('Authorization', `Bearer ${token}`)
  config.headers = headers
  return config
})

export const v2Api = {
  listContent: (params?: Record<string, unknown>) => v2.get('/content', { params }),
  getContent: (id: number) => v2.get(`/content/${id}`),
  listTargets: (platform?: string) => v2.get('/accounts/targets', { params: { platform } }),
  listCredentials: (platform?: string) => v2.get('/accounts/credentials', { params: { platform } }),
  listAllAccounts: () => v2.get('/accounts/all'),
  createTarget: (data: Record<string, unknown>) => v2.post('/accounts/targets', data),
  deleteTarget: (id: number) => v2.delete(`/accounts/targets/${id}`),
  toggleTarget: (id: number) => v2.post(`/accounts/targets/${id}/toggle`),
  refreshTarget: (id: number) => v2.post(`/accounts/targets/${id}/refresh`),
  refreshAllTargets: () => v2.post('/accounts/targets/actions/refresh-all'),
  deleteCredential: (id: number) => v2.delete(`/accounts/credentials/${id}`),
  toggleCredential: (id: number) => v2.post(`/accounts/credentials/${id}/toggle`),
  getQQQrcode: () => v2.get('/auth/qq/qrcode'),
  getQQStatus: () => v2.get('/auth/qq/status'),
  getQQDebugView: () => v2.get('/auth/qq/debug-view'),
  refreshQQCookies: () => v2.post('/auth/qq/refresh-cookies'),
  qqNapcatLogin: () => v2.post('/auth/qq/napcat-login'),
  getXHSQrcode: () => v2.get('/auth/xhs/qrcode'),
  getXHSStatus: () => v2.get('/auth/xhs/status'),
  getXHSDebugView: () => v2.get('/auth/xhs/debug-view'),
  xhsNavigate: (url: string) => v2.post('/auth/xhs/navigate', { url }),
  xhsSaveCookies: () => v2.post('/auth/xhs/save-cookies'),
  xhsResetBrowser: () => v2.post('/auth/xhs/reset-browser'),
  createCrawl: (data: {
    platform: string
    mode?: string
    target_account_ids?: number[]
    credential_id?: number
    run_inline?: boolean
  }) => v2.post('/crawl', data),
  latestCrawl: (platform: string) => v2.get('/crawl/latest', { params: { platform } }),
  getCrawlJob: (id: number) => v2.get(`/crawl/${id}`),
  searchKnowledge: (data: { query: string; platform?: string; author?: string; top_k?: number }) =>
    v2.post('/knowledge/search', data),
  indexKnowledge: (force = false) => v2.post('/knowledge/index', null, { params: { force } }),
  listAgentSessions: () => v2.get('/agent/sessions'),
  createAgentSession: (title = '新对话') => v2.post('/agent/sessions', { title }),
  deleteAgentSession: (id: number) => v2.delete(`/agent/sessions/${id}`),
  listAgentMessages: (id: number) => v2.get(`/agent/sessions/${id}/messages`),
  sendAgentMessage: (sessionId: number, content: string) =>
    fetch(`/api/v2/agent/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: buildAdminAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ content }),
    }),
  listTasks: () => v2.get('/tasks'),
  createTask: (data: Record<string, unknown>) => v2.post('/tasks', data),
  updateTask: (id: number, data: Record<string, unknown>) => v2.put(`/tasks/${id}`, data),
  deleteTask: (id: number) => v2.delete(`/tasks/${id}`),
  toggleTask: (id: number) => v2.post(`/tasks/${id}/toggle`),
  runTaskNow: (id: number) => v2.post(`/tasks/${id}/run-now`),
  listTaskRuns: (id: number) => v2.get(`/tasks/${id}/logs`),
  listProviders: () => v2.get('/providers'),
  createProvider: (data: Record<string, unknown>) => v2.post('/providers', data),
  updateProvider: (id: number, data: Record<string, unknown>) => v2.put(`/providers/${id}`, data),
  deleteProvider: (id: number) => v2.delete(`/providers/${id}`),
  activateProvider: (id: number) => v2.post(`/providers/${id}/activate`),
  testProvider: (data: Record<string, unknown>) => v2.post('/providers/test-connection', data),
  readSettings: () => v2.get('/system/settings'),
  updateSettings: (values: Record<string, unknown>) => v2.put('/system/settings', { values }),
  updateInfrastructure: (values: Record<string, string>) => v2.put('/system/infrastructure', { values }),
  readRuntime: () => v2.get('/system/runtime'),
  readStats: () => v2.get('/system/stats'),
  readNapcatStatus: () => v2.get('/system/napcat-status'),
  readDatabaseConfig: () => v2.get('/system/database'),
  testDatabaseConnection: (data: Record<string, unknown>) => v2.post('/system/database/test', data),
  readRedisConfig: () => v2.get('/system/redis'),
  testRedisConnection: (url = '') => v2.post('/system/redis/test', { url }),
  readLogs: (params?: Record<string, unknown>) => v2.get('/system/logs', { params }),
  clearLogs: () => v2.delete('/system/logs'),
  readSummaryStats: () => v2.get('/system/summary/stats'),
  generateSummaries: (forceAll = false) => v2.post('/system/summary/generate', null, { params: { force_all: forceAll } }),
}

export default v2
