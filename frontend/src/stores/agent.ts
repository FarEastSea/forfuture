import { defineStore } from 'pinia'
import { v2Api } from '@/api/v2'

export const useAgentStore = defineStore('agent', {
  state: () => ({ sessions: [] as any[], loading: false }),
  actions: {
    async refresh() {
      this.loading = true
      try {
        const { data } = await v2Api.listAgentSessions()
        this.sessions = data.items || []
      } finally { this.loading = false }
    },
  },
})
