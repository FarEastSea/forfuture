<template>
  <div class="page-container">
    <div class="page-header">
      <h2>QQ空间动态</h2>
      <div class="header-actions">
        <a-select v-model="selectedQQ" placeholder="筛选QQ号" allow-clear style="width: 180px" @change="loadPosts">
          <a-option v-for="a in accounts" :key="a.account_id" :value="a.account_id">
            {{ a.nickname || a.account_id }}
          </a-option>
        </a-select>
        <a-select v-model="selectedLoginAccountId" placeholder="自动选择登录账号" allow-clear style="width: 190px">
          <a-option v-for="a in loginAccounts" :key="a.id" :value="a.id">
            {{ a.nickname || a.account_id }}
          </a-option>
        </a-select>
        <a-radio-group v-model="crawlMode" type="button" size="small" style="margin-right: 8px;">
          <a-radio value="incremental">增量更新</a-radio>
          <a-radio value="overwrite">覆盖更新</a-radio>
        </a-radio-group>
        <a-button type="primary" :loading="crawling" @click="startCrawl">
          <template #icon><icon-sync /></template>
          抓取动态
        </a-button>
      </div>
    </div>

    <div v-if="postsError" class="inline-state-card error compact">
      <div>
        <strong>动态列表加载失败</strong>
        <p>{{ postsError }}</p>
      </div>
      <a-button size="small" type="outline" @click="loadPosts">重新加载</a-button>
    </div>

    <div v-if="crawlStatus" class="crawl-status">
      <a-alert :type="crawlStatus.error ? 'error' : crawlStatus.running ? 'info' : 'success'" :closable="!crawlStatus.running">
        <template #title v-if="crawlStatus.error">抓取错误</template>
        {{ crawlStatus.error || crawlStatus.progress || '空闲' }}
      </a-alert>
      <div v-if="crawlStatus.details && crawlStatus.details.length > 0" style="margin-top: 8px;">
        <div v-for="(d, i) in crawlStatus.details" :key="i" class="log-item" style="padding: 3px 0; font-size: 12px;">
          <span class="log-time" style="min-width: 60px;">{{ d.time }}</span>
          <a-tag :color="d.level === 'info' ? 'green' : d.level === 'warning' ? 'orange' : 'red'" size="small">{{ d.level }}</a-tag>
          <span class="log-reason">{{ d.msg }}</span>
        </div>
      </div>
    </div>

    <div class="timeline-container">
      <a-spin :loading="loading" tip="加载中..." style="width: 100%">
        <div v-if="posts.length === 0 && !loading && !postsError" class="empty-state">
          <icon-empty :size="48" />
          <p>暂无动态记录</p>
          <p class="sub-text">请先在设置中添加要监控的QQ号，然后点击"抓取动态"</p>
          <a-button type="outline" :disabled="!accounts.length" @click="startCrawl">立即抓取</a-button>
        </div>

        <div v-for="post in posts" :key="post.id" class="post-card">
          <div class="post-header">
            <img v-if="post.author_avatar" :src="getImageSrc(post.author_avatar)" class="avatar" alt="" @error="onAvatarError($event, post)" />
            <div v-else class="avatar avatar-placeholder">{{ (post.author_nickname || '?')[0] }}</div>
            <div class="post-meta">
              <span class="nickname">{{ post.author_nickname || '未知用户' }}</span>
              <a-tooltip v-if="getCurrentNickname(post) && getCurrentNickname(post) !== post.author_nickname" :content="`当前昵称: ${getCurrentNickname(post)}`">
                <a-tag size="small" color="orangered" style="margin-left:4px; font-size:10px;">曾用名</a-tag>
              </a-tooltip>
              <span class="post-time">{{ formatTime(post.post_time) }}</span>
            </div>
          </div>

          <div class="post-content" v-if="post.content">{{ post.content }}</div>

          <div class="post-meta-tags" v-if="post.device_info || post.location || (post.edit_history && post.edit_history.length > 0)">
            <a-tag v-if="post.device_info" size="small" color="arcoblue">{{ post.device_info }}</a-tag>
            <a-tag v-if="post.location" size="small" color="green">{{ post.location }}</a-tag>
            <a-tooltip v-if="post.edit_history && post.edit_history.length > 0">
              <template #content>
                <div style="max-width: 300px;">
                  <div v-for="(h, hi) in post.edit_history.slice(-5)" :key="hi" style="font-size:11px; margin-bottom:4px;">
                    <div style="color:#999;">{{ h.time }}</div>
                    <div v-for="(key, ci) in Object.keys(h.changes || {})" :key="ci">
                      {{ key }}: {{ typeof h.changes[key] === 'object' ? (h.changes[key].old + ' → ' + h.changes[key].new) : h.changes[key] }}
                    </div>
                  </div>
                </div>
              </template>
              <a-tag size="small" color="orangered">已编辑 ({{ post.edit_history.length }}次)</a-tag>
            </a-tooltip>
          </div>

          <div class="post-images" v-if="post.images && post.images.length > 0">
            <a-image-preview-group>
              <a-image
                v-for="(img, idx) in post.images"
                :key="idx"
                :src="getImageSrc(img)"
                :width="imgSize(post.images.length)"
                fit="cover"
                class="post-image"
              />
            </a-image-preview-group>
          </div>

          <div class="post-video" v-if="post.local_video_path || post.video_url">
            <video
              controls
              playsinline
              preload="none"
              :poster="post.images && post.images.length > 0 ? getImageSrc(post.images[0]) : undefined"
              :src="getVideoSrc(post)"
              class="post-video-player"
            ></video>
          </div>

          <div class="post-forward" v-if="post.forward_content">
            <div class="forward-tag">转发</div>
            {{ post.forward_content }}
          </div>

          <div class="post-stats">
            <span><icon-heart /> {{ post.like_count || 0 }}</span>
            <span><icon-message /> {{ post.comment_count || 0 }}</span>
          </div>

          <div class="post-comments" v-if="post.comments && post.comments.length > 0">
            <div v-for="c in post.comments" :key="c.id" class="comment-item">
              <span class="comment-author">{{ c.author_nickname || '匿名' }}</span>
              <span class="comment-text">{{ c.content }}</span>
              <span class="comment-time">{{ formatTime(c.comment_time) }}</span>
            </div>
          </div>
        </div>

        <div class="pagination-wrap" v-if="total > pageSize">
          <a-pagination :total="total" :page-size="pageSize" v-model:current="page" @change="loadPosts" />
        </div>
      </a-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { authApi, qqApi } from '@/api'
import { IconSync, IconHeart, IconMessage, IconEmpty } from '@arco-design/web-vue/es/icon'

const posts = ref<any[]>([])
const accounts = ref<any[]>([])
const loginAccounts = ref<any[]>([])
const selectedQQ = ref<string>('')
const selectedLoginAccountId = ref<number | undefined>(undefined)
const loading = ref(false)
const crawling = ref(false)
const postsError = ref('')
const crawlMode = ref('incremental')
const crawlStatus = ref<any>(null)
const page = ref(1)
const pageSize = 20
const total = ref(0)
let crawlStatusTimer: ReturnType<typeof setInterval> | null = null

async function loadPosts() {
  loading.value = true
  postsError.value = ''
  try {
    const params: any = { page: page.value, page_size: pageSize }
    if (selectedQQ.value) params.qq_number = selectedQQ.value
    const { data } = await qqApi.getPosts(params)
    posts.value = data.items || []
    total.value = data.total || 0
  } catch (error) {
    posts.value = []
    total.value = 0
    postsError.value = getErrorMessage(error, '暂时无法读取动态列表，请稍后重试。')
  } finally {
    loading.value = false
  }
}

async function loadAccounts() {
  try {
    const { data } = await qqApi.getAccounts()
    accounts.value = data || []
  } catch (error) {
    Message.error(getErrorMessage(error, '监控账号加载失败，请检查设置页配置。'))
  }
}

async function loadLoginAccounts() {
  try {
    const { data } = await authApi.getAccounts('qq')
    loginAccounts.value = (data || []).filter((a: any) => a.is_target === 0)
  } catch (error) {
    Message.error(getErrorMessage(error, 'QQ登录账号加载失败，请检查设置页配置。'))
  }
}

async function startCrawl() {
  const ids = accounts.value.map((a: any) => a.account_id)
  if (ids.length === 0) {
    Message.warning('请先在设置中添加要监控的QQ号')
    return
  }
  crawling.value = true
  try {
    await qqApi.crawl(ids, crawlMode.value, {
      login_account_id: selectedLoginAccountId.value || undefined,
    })
    pollCrawlStatus()
  } catch (error) {
    crawling.value = false
    crawlStatus.value = {
      error: getErrorMessage(error, 'QQ 抓取任务启动失败，请稍后再试。'),
      running: false,
    }
    Message.error(crawlStatus.value.error)
  }
}

async function pollCrawlStatus() {
  if (crawlStatusTimer) {
    clearInterval(crawlStatusTimer)
  }

  crawlStatusTimer = setInterval(async () => {
    try {
      const { data } = await qqApi.getCrawlStatus()
      crawlStatus.value = data
      if (!data.running) {
        if (crawlStatusTimer) {
          clearInterval(crawlStatusTimer)
          crawlStatusTimer = null
        }
        crawling.value = false
        void loadPosts()
      }
    } catch (error) {
      if (crawlStatusTimer) {
        clearInterval(crawlStatusTimer)
        crawlStatusTimer = null
      }
      crawling.value = false
      crawlStatus.value = {
        error: getErrorMessage(error, 'QQ 抓取状态更新失败，请稍后手动刷新。'),
        running: false,
      }
    }
  }, 2000)
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) {
    return error.message
  }

  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as any).response?.data?.detail
    if (typeof detail === 'string' && detail) {
      return detail
    }
  }

  return fallback
}

function formatTime(t: string | null) {
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return d.toLocaleDateString('zh-CN')
}

function getImageSrc(img: any): string {
  // 优先使用本地路径（已下载到服务器的图片）
  if (typeof img === 'string') {
    if (img.startsWith('/static/') || img.startsWith('/api/')) return img
    if (img.startsWith('http')) return `/api/proxy/image?url=${encodeURIComponent(img)}`
    return img
  }
  if (img.local_path) return img.local_path
  // 如果无本地路径，用代理URL避免CORS问题
  if (img.url) return `/api/proxy/image?url=${encodeURIComponent(img.url)}`
  return ''
}

function onAvatarError(event: Event, post: any) {
  // avatar加载失败时回退到q.qlogo.cn CDN
  const target = event.target as HTMLImageElement
  if (!target || target.dataset.fallback === '1') return
  target.dataset.fallback = '1'
  const qq = post.author_qq || post.qq_number || ''
  if (qq) {
    target.src = `/api/proxy/image?url=${encodeURIComponent(`https://q.qlogo.cn/headimg_dl?dst_uin=${qq}&spec=640&img_type=jpg`)}`
  }
}

function getVideoSrc(post: any): string {
  if (post.local_video_path) return post.local_video_path
  if (post.video_url) return `/api/proxy/image?url=${encodeURIComponent(post.video_url)}`
  return ''
}

function imgSize(count: number) {
  if (count === 1) return 320
  if (count <= 4) return 150
  return 120
}

function getCurrentNickname(post: any): string {
  const qq = post.author_qq || post.qq_number || ''
  if (!qq) return ''
  const acc = accounts.value.find((a: any) => a.account_id === qq)
  return acc?.nickname || ''
}

onMounted(() => {
  loadAccounts()
  loadLoginAccounts()
  loadPosts()
})

onUnmounted(() => {
  if (crawlStatusTimer) {
    clearInterval(crawlStatusTimer)
  }
})
</script>
