<template>
  <div class="page-container">
    <div class="page-header">
      <h2>知识库</h2>
      <div class="header-actions">
        <a-select v-model="platform" placeholder="全部平台" allow-clear style="width: 140px" @change="runSearch">
          <a-option value="qq">QQ 空间</a-option>
          <a-option value="xhs">小红书</a-option>
        </a-select>
        <a-input-search
          v-model="query"
          placeholder="按关键词检索已采集记录"
          style="width: 320px"
          allow-clear
          @search="runSearch"
          @press-enter="runSearch"
        />
        <a-button type="primary" :loading="loading" @click="runSearch">检索</a-button>
        <a-button :loading="indexing" @click="rebuildIndex">回填索引</a-button>
      </div>
    </div>

    <div v-if="error" class="inline-state-card error compact">
      <div>
        <strong>检索失败</strong>
        <p>{{ error }}</p>
      </div>
      <a-button size="small" type="outline" @click="runSearch">重试</a-button>
    </div>

    <a-spin :loading="loading" style="width: 100%">
      <div v-if="!hits.length && !loading && !error" class="empty-state">
        <icon-empty :size="48" />
        <p>还没有检索结果</p>
        <p class="sub-text">先采集内容，必要时点一次「回填索引」，再用关键词搜索。</p>
      </div>

      <div v-for="hit in hits" :key="hit.chunk_id" class="post-card" @click="openHit(hit)">
        <div class="post-header">
          <div class="post-meta">
            <span class="nickname">{{ hit.author_name || '未知作者' }}</span>
            <a-tag size="small" :color="hit.platform === 'qq' ? 'arcoblue' : 'red'">
              {{ hit.platform === 'qq' ? 'QQ' : '小红书' }}
            </a-tag>
            <a-tag size="small">{{ hit.chunk_type }}</a-tag>
            <span class="post-time">{{ formatTime(hit.posted_at) }}</span>
          </div>
        </div>
        <div class="post-content">{{ hit.text }}</div>
        <div class="post-stats">
          <span>相关度 {{ Number(hit.score || 0).toFixed(3) }}</span>
          <span>记录 #{{ hit.content_item_id }}</span>
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconEmpty } from '@arco-design/web-vue/es/icon'
import { useRouter } from 'vue-router'
import { v2Api } from '@/api/v2'

const router = useRouter()
const query = ref('')
const platform = ref<string | undefined>(undefined)
const hits = ref<any[]>([])
const loading = ref(false)
const indexing = ref(false)
const error = ref('')

async function runSearch() {
  const text = query.value.trim()
  if (!text) {
    hits.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await v2Api.searchKnowledge({
      query: text,
      platform: platform.value,
      top_k: 20,
    })
    hits.value = data.items || []
  } catch (err) {
    hits.value = []
    error.value = err instanceof Error ? err.message : '检索失败'
  } finally {
    loading.value = false
  }
}

async function rebuildIndex() {
  indexing.value = true
  try {
    const { data } = await v2Api.indexKnowledge(false)
    if (data.embedding_status === 'degraded') {
      Message.warning(
        `全文索引已可用：${data.total_chunks} 个分块；向量 ${data.embedded_chunks} 个，嵌入模型当前不可用`,
      )
    } else {
      Message.success(`索引已就绪：${data.total_chunks} 个分块（${data.items} 条内容）`)
    }
  } catch (err) {
    Message.error(err instanceof Error ? err.message : '索引回填失败')
  } finally {
    indexing.value = false
  }
}

function openHit(hit: any) {
  router.push(hit.platform === 'xhs' ? '/xhs' : '/qq')
}

function formatTime(t: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(() => {
  query.value = ''
})
</script>
