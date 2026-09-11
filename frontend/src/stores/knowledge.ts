import { defineStore } from 'pinia'
import { ref } from 'vue'
import { v2Api } from '@/api/v2'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const hits = ref<any[]>([])
  const loading = ref(false)

  async function search(query: string, platform?: string) {
    loading.value = true
    try {
      const { data } = await v2Api.searchKnowledge({ query, platform, top_k: 20 })
      hits.value = data.items || []
      return hits.value
    } finally {
      loading.value = false
    }
  }

  return { hits, loading, search }
})
