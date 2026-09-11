import { defineStore } from 'pinia'

import { openAdminWebSocket } from '@/utils/adminToken'

export const useRealtimeStore = defineStore('realtime', {
  state: () => ({
    status: 'disconnected' as 'connected' | 'connecting' | 'disconnected',
    napcat: { connected_count: 0, connections: [] as any[] },
    crawls: {} as Record<string, any>,
    credentials: {} as Record<number, any>,
    reconnectTimer: null as ReturnType<typeof setTimeout> | null,
    socket: null as WebSocket | null,
  }),
  actions: {
    connect() {
      if (this.socket?.readyState === WebSocket.OPEN) return
      this.disconnect(false)
      this.status = 'connecting'
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = openAdminWebSocket(`${protocol}//${location.host}/ws/v2/events`)
      this.socket = socket
      socket.onopen = () => { this.status = 'connected' }
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data)
          const payload = event.payload || {}
          if (event.type === 'napcat.status') this.napcat = payload
          if (event.type?.startsWith('crawl.job.')) {
            const key = payload.platform || payload.id
            this.crawls[key] = { ...this.crawls[key], ...payload, event_type: event.type }
          }
          if (event.type === 'credential.status.changed' && payload.id) this.credentials[payload.id] = payload
        } catch { /* ignore malformed events */ }
      }
      socket.onerror = () => socket.close()
      socket.onclose = () => {
        this.status = 'disconnected'
        if (this.socket === socket) this.socket = null
        this.reconnectTimer = setTimeout(() => this.connect(), 5000)
      }
    },
    disconnect(reconnect = false) {
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
      const socket = this.socket
      this.socket = null
      if (socket) {
        socket.onclose = null
        socket.close()
      }
      this.status = 'disconnected'
      if (reconnect) this.reconnectTimer = setTimeout(() => this.connect(), 5000)
    },
  },
})
