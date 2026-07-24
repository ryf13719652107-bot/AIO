import axios from 'axios'
import { useAuthStore } from '../store/auth'
import { ElMessage } from 'element-plus'
import router from '../router'

const api = axios.create({ baseURL: '/api', timeout: 60000 })

api.interceptors.request.use((cfg) => {
  const auth = useAuthStore()
  if (auth.token) cfg.headers.Authorization = `Bearer ${auth.token}`
  return cfg
})

api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    if (err.response?.status === 401) {
      useAuthStore().logout()
      router.push({ name: 'login' })
    }
    ElMessage.error(msg || '请求失败')
    return Promise.reject(err)
  },
)

export const authApi = {
  login: (username, password) => api.post('/auth/login', { username, password }),
  me: () => api.get('/auth/me'),
}

export const configApi = {
  get: () => api.get('/config'),
  update: (payload) => api.put('/config', payload, { timeout: 60000 }),
  getExchange: () => api.get('/config/exchange'),
  updateExchange: (payload) => api.put('/config/exchange', payload),
  setSymbolEnabled: (symbol, enabled) =>
    api.put(`/config/symbol/${encodeURIComponent(symbol)}/enabled`, { enabled }),
}

export const controlApi = {
  status: () => api.get('/control/status'),
  start: () => api.post('/control/start', null, { timeout: 120000 }),
  stop: () => api.post('/control/stop', null, { timeout: 60000 }),
  runtime: () => api.get('/control/runtime'),
  balance: () => api.get('/control/balance'),
  trades: (limit = 200) => api.get('/control/trades', { params: { limit } }),
  logs: (limit = 200) => api.get('/control/logs', { params: { limit } }),
  emergencyClose: (symbol) => api.post(`/control/close/${symbol}`),
  closeAll: () => api.post('/control/close-all', null, { timeout: 120000 }),
}

export function openStream(token, onMessage) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/api/ws/stream?token=${encodeURIComponent(token)}`
  const ws = new WebSocket(url)
  ws.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)) } catch {}
  }
  return ws
}

export default api
