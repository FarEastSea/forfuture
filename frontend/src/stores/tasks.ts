import { defineStore } from 'pinia'
import { v2Api } from '@/api/v2'

export const useTasksStore = defineStore('tasks', {
  state: () => ({ items: [] as any[], loading: false }),
  actions: {
    async refresh() {
      this.loading = true
      try {
        const { data } = await v2Api.listTasks()
        this.items = data.items || []
      } finally { this.loading = false }
    },
  },
})
