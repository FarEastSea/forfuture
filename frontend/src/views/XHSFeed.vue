<template>
  <div class="page-container">
    <div class="page-header">
      <h2>小红书笔记</h2>
      <div class="header-actions">
        <a-select v-model="selectedUid" placeholder="筛选账号" allow-clear style="width: 180px" @change="loadNotes">
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
          抓取笔记
        </a-button>
      </div>
    </div>

    <div v-if="notesError" class="inline-state-card error compact">
      <div>
        <strong>笔记列表加载失败</strong>
        <p>{{ notesError }}</p>
      </div>
      <a-button size="small" type="outline" @click="loadNotes">重新加载</a-button>
    </div>

    <div v-if="crawlStatus" class="crawl-status">
      <a-alert :type="crawlStatus.error ? 'error' : 'info'">
        {{ crawlStatus.error || crawlStatus.progress || '空闲' }}
      </a-alert>
    </div>

    <a-spin :loading="loading" tip="加载中..." style="width: 100%">
      <div v-if="notes.length === 0 && !loading && !notesError" class="empty-state">
        <icon-empty :size="48" />
        <p>暂无笔记记录</p>
        <p class="sub-text">请先在设置中添加要监控的小红书账号，然后点击"抓取笔记"</p>
        <a-button type="outline" :disabled="!accounts.length" @click="startCrawl">立即抓取</a-button>
      </div>

      <div class="xhs-grid">
        <div v-for="note in notes" :key="note.id" class="note-card" @click="showDetail(note)">
          <div class="note-cover">
            <img v-if="getCover(note)" :src="getCover(note)" alt="" />
            <div v-else class="cover-placeholder"><icon-image :size="32" /></div>
            <div class="note-type-badge" v-if="note.video_url || note.note_type === 'video'">视频</div>
            <div class="note-type-badge missing" v-if="hasMissingXhsImages(note.images)">待修复媒体</div>
          </div>
          <div class="note-info">
            <div class="note-title">{{ note.title || '无标题' }}</div>
            <div class="note-author">
              <img v-if="note.author_avatar" :src="getAvatarSrc(note.author_avatar)" class="mini-avatar" alt="" />
              <span>{{ note.author_nickname || '未知' }}</span>
            </div>
            <div class="note-stats">
              <span><icon-heart /> {{ note.like_count || 0 }}</span>
              <span><icon-star /> {{ note.collect_count || 0 }}</span>
              <span><icon-message /> {{ note.comment_count || 0 }}</span>
              <span v-if="note.share_count"><icon-share-alt /> {{ note.share_count }}</span>
            </div>
            <div class="note-meta-extra" v-if="note.device_info || note.location || note.ip_location || note.video_duration || (note.edit_history && note.edit_history.length)">
              <a-tag v-if="note.device_info" size="small" color="arcoblue">{{ note.device_info }}</a-tag>
              <a-tag v-if="note.ip_location" size="small" color="cyan">IP: {{ note.ip_location }}</a-tag>
              <a-tag v-if="note.location && note.location !== ('IP属地: ' + note.ip_location)" size="small" color="green">{{ note.location }}</a-tag>
              <a-tag v-if="note.video_duration" size="small" color="purple">{{ formatDuration(note.video_duration) }}</a-tag>
              <a-tag v-if="note.edit_history && note.edit_history.length" size="small" color="orangered">已编辑</a-tag>
            </div>
          </div>
        </div>
      </div>

      <div class="pagination-wrap" v-if="total > pageSize">
        <a-pagination :total="total" :page-size="pageSize" v-model:current="page" @change="loadNotes" />
      </div>
    </a-spin>

    <a-modal v-model:visible="detailVisible" :title="detailNote?.title || '笔记详情'" width="600px" :footer="false">
      <div v-if="detailNote" class="note-detail">
        <div class="detail-author">
          <img v-if="detailNote.author_avatar" :src="getAvatarSrc(detailNote.author_avatar)" class="avatar" alt="" />
          <div>
            <div class="nickname">{{ detailNote.author_nickname }}</div>
            <div class="post-time">{{ formatTime(detailNote.post_time) }}</div>
          </div>
        </div>
        <div class="detail-images" v-if="getLocalXhsImages(detailNote.images).length">
          <a-image-preview-group>
            <a-image v-for="(img, i) in getLocalXhsImages(detailNote.images)" :key="i" :src="getXhsImageSrc(img)" width="100%" fit="contain" />
          </a-image-preview-group>
        </div>
        <p v-if="hasMissingXhsImages(detailNote.images)" class="media-missing-tip">部分图片未下载到本地，请重新抓取修复。</p>
        <div class="detail-video" v-if="detailNote.local_video_path">
          <video
            controls
            :src="getXhsVideoSrc(detailNote)"
            style="width: 100%; max-height: 400px; border-radius: 8px; background: #000"
            @error="onXhsVideoError(detailNote)"
          ></video>
        </div>
        <p v-if="detailNote.video_error" class="media-missing-tip">{{ detailNote.video_error }}</p>
        <p v-else-if="detailNote.video_url" class="media-missing-tip">视频未下载到本地，请重新登录后重新抓取修复。</p>
        <div class="detail-content">{{ detailNote.content }}</div>
        <div class="detail-meta-row" v-if="detailNote.device_info || detailNote.location || detailNote.ip_location || detailNote.note_type || detailNote.video_duration">
          <a-tag v-if="detailNote.note_type" size="small" :color="detailNote.note_type === 'video' ? 'purple' : 'blue'">{{ detailNote.note_type === 'video' ? '视频笔记' : '图文笔记' }}</a-tag>
          <a-tag v-if="detailNote.device_info" size="small" color="arcoblue">{{ detailNote.device_info }}</a-tag>
          <a-tag v-if="detailNote.ip_location" size="small" color="cyan">IP: {{ detailNote.ip_location }}</a-tag>
          <a-tag v-if="detailNote.location && detailNote.location !== ('IP属地: ' + detailNote.ip_location)" size="small" color="green">{{ detailNote.location }}</a-tag>
          <a-tag v-if="detailNote.video_duration" size="small" color="purple">时长: {{ formatDuration(detailNote.video_duration) }}</a-tag>
        </div>
        <div class="detail-at-users" v-if="detailNote.at_user_list && detailNote.at_user_list.length">
          <span class="at-label">@用户: </span>
          <a-tag v-for="u in detailNote.at_user_list" :key="u.user_id" size="small" color="orangered">@{{ u.nickname || u.user_id }}</a-tag>
        </div>
        <div class="detail-tags" v-if="detailNote.tags && detailNote.tags.length">
          <a-tag v-for="tag in detailNote.tags" :key="tag" color="arcoblue">{{ tag }}</a-tag>
        </div>
        <div class="detail-edit-history" v-if="detailNote.edit_history && detailNote.edit_history.length">
          <h4>编辑记录 ({{ detailNote.edit_history.length }}次)</h4>
          <div v-for="(h, hi) in detailNote.edit_history" :key="hi" class="edit-history-item">
            <div class="edit-time">{{ h.time }}</div>
            <div v-for="(c, ci) in h.changes" :key="ci" class="edit-change">{{ c }}</div>
          </div>
        </div>
        <div class="detail-comments-header">
          <h4 v-if="detailNote.comments && detailNote.comments.length">评论 ({{ detailNote.comment_count || detailNote.comments.length }})</h4>
          <h4 v-else>评论 ({{ detailNote.comment_count || 0 }})</h4>
          <a-button size="small" type="outline" :loading="crawlingComments" @click="crawlNoteComments(detailNote)">
            <template #icon><icon-sync /></template>
            抓取评论
          </a-button>
        </div>
        <p v-if="commentsFeedback" class="sub-text">{{ commentsFeedback }}</p>
        <div v-if="detailNote.comments && detailNote.comments.length" class="detail-comments">
          <div v-for="c in detailNote.comments" :key="c.id" class="comment-item">
            <div class="comment-main">
              <img v-if="c.author_avatar" :src="getAvatarSrc(c.author_avatar)" class="comment-avatar" alt="" />
              <div v-else class="comment-avatar comment-avatar-placeholder">{{ (c.author_nickname || '?')[0] }}</div>
              <div class="comment-body">
                <div class="comment-header">
                  <span class="comment-author">{{ c.author_nickname }}</span>
                  <a-tag v-if="c.is_author" size="small" color="red" class="author-badge">作者</a-tag>
                  <span class="comment-time" v-if="c.comment_time">{{ formatTime(c.comment_time) }}</span>
                  <span class="comment-ip" v-if="c.ip_location">{{ c.ip_location }}</span>
                </div>
                <div class="comment-text">{{ c.content }}</div>
                <div class="comment-footer" v-if="c.like_count">
                  <span class="comment-likes"><icon-heart /> {{ c.like_count }}</span>
                </div>
              </div>
            </div>
            <div class="comment-replies" v-if="c.replies && c.replies.length">
              <div v-for="r in c.replies" :key="r.id" class="reply-item">
                <img v-if="r.author_avatar" :src="getAvatarSrc(r.author_avatar)" class="reply-avatar" alt="" />
                <div v-else class="reply-avatar comment-avatar-placeholder">{{ (r.author_nickname || '?')[0] }}</div>
                <div class="comment-body">
                  <div class="comment-header">
                    <span class="comment-author">{{ r.author_nickname }}</span>
                    <a-tag v-if="r.is_author" size="small" color="red" class="author-badge">作者</a-tag>
                    <span class="reply-target" v-if="r.target_nickname">回复 <b>{{ r.target_nickname }}</b></span>
                    <span class="comment-time" v-if="r.comment_time">{{ formatTime(r.comment_time) }}</span>
                    <span class="comment-ip" v-if="r.ip_location">{{ r.ip_location }}</span>
                  </div>
                  <div class="comment-text">{{ r.content }}</div>
                  <div class="comment-footer" v-if="r.like_count">
                    <span class="comment-likes"><icon-heart /> {{ r.like_count }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { toXhsNote } from '@/api/adapters'
import { v2Api } from '@/api/v2'
import { useRealtimeStore } from '@/stores/realtime'
import { IconSync, IconHeart, IconStar, IconEmpty, IconImage, IconShareAlt, IconMessage } from '@arco-design/web-vue/es/icon'

const notes = ref<any[]>([])
const accounts = ref<any[]>([])
const loginAccounts = ref<any[]>([])
const selectedUid = ref('')
const selectedLoginAccountId = ref<number | undefined>(undefined)
const loading = ref(false)
const crawling = ref(false)
const notesError = ref('')
const crawlMode = ref('incremental')
const crawlStatus = ref<any>(null)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const detailVisible = ref(false)
const detailNote = ref<any>(null)
const crawlingComments = ref(false)
const commentsFeedback = ref('')
let commentsRefreshTimer: ReturnType<typeof setTimeout> | null = null
const realtime = useRealtimeStore()

async function loadNotes() {
  loading.value = true
  notesError.value = ''
  try {
    const params: any = { platform: 'xhs', page: page.value, page_size: pageSize }
    if (selectedUid.value) params.account_id = selectedUid.value
    const { data } = await v2Api.listContent(params)
    notes.value = (data.items || []).map(toXhsNote)
    total.value = data.total || 0
  } catch (error) {
    notes.value = []
    total.value = 0
    notesError.value = getErrorMessage(error, '暂时无法读取笔记列表，请稍后重试。')
  } finally { loading.value = false }
}

async function loadAccounts() {
  try {
    const { data } = await v2Api.listTargets('xhs')
    accounts.value = data.items || []
  } catch (error) {
    Message.error(getErrorMessage(error, '监控账号加载失败，请检查设置页配置。'))
  }
}

async function loadLoginAccounts() {
  try {
    const { data } = await v2Api.listCredentials('xhs')
    loginAccounts.value = data.items || []
  } catch (error) {
    Message.error(getErrorMessage(error, '小红书登录账号加载失败，请检查设置页配置。'))
  }
}

async function startCrawl() {
  if (!accounts.value.length) {
    Message.warning('请先在设置中添加要监控的小红书账号')
    return
  }
  crawling.value = true
  try {
    await v2Api.createCrawl({
      platform: 'xhs',
      mode: crawlMode.value,
      target_account_ids: accounts.value.map((a: any) => a.id).filter(Boolean),
      credential_id: selectedLoginAccountId.value || undefined,
    })
  } catch (error) {
    crawling.value = false
    crawlStatus.value = { error: getErrorMessage(error, '小红书抓取任务启动失败，请稍后再试。'), running: false }
    Message.error(crawlStatus.value.error)
  }
}

watch(() => realtime.crawls.xhs, (event) => {
  if (!event) return
  const finished = event.event_type === 'crawl.job.finished'
  crawling.value = !finished
  crawlStatus.value = { ...event, running: !finished, progress: event.message || event.status }
  if (finished) void loadNotes()
}, { deep: true })

async function showDetail(note: any) {
  try {
    const { data } = await v2Api.getContent(note.id)
    detailNote.value = toXhsNote(data)
    detailVisible.value = true
    commentsFeedback.value = ''
  } catch {
    detailNote.value = note
    detailVisible.value = true
    commentsFeedback.value = ''
  }
}

async function crawlNoteComments(note: any) {
  if (!note?.id) return
  crawlingComments.value = true
  commentsFeedback.value = '评论抓取任务已提交，预计 15 秒内可刷新到最新内容。'
  try {
    await v2Api.createCrawl({ platform: 'xhs', mode: 'repair', target_account_ids: note.target_account_id ? [note.target_account_id] : undefined })
    Message.info('修复采集任务已启动，完成后会自动刷新详情。')

    if (commentsRefreshTimer) {
      clearTimeout(commentsRefreshTimer)
    }

    commentsRefreshTimer = setTimeout(async () => {
      try {
        const { data } = await v2Api.getContent(note.id)
        detailNote.value = toXhsNote(data)
        commentsFeedback.value = '最新评论已自动刷新。'
      } catch {
        commentsFeedback.value = '评论抓取完成，但自动刷新失败，请手动重开详情查看。'
      }
      crawlingComments.value = false
    }, 15000)
  } catch (error) {
    crawlingComments.value = false
    commentsFeedback.value = ''
    Message.error(getErrorMessage(error, '评论抓取任务启动失败，请稍后再试。'))
  }
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

function getCover(note: any): string {
  const localImages = getLocalXhsImages(note.images || [])
  return localImages.length > 0 ? getXhsImageSrc(localImages[0]) : ''
}

function normalizeRemoteMediaUrl(url: string): string {
  if (url.startsWith('//')) return `https:${url}`
  return url
}

function buildProxyMediaUrl(url: string): string {
  return `/api/v2/media/proxy?url=${encodeURIComponent(normalizeRemoteMediaUrl(url))}`
}

function getAvatarSrc(url: string): string {
  if (!url) return ''
  url = normalizeRemoteMediaUrl(url)
  if (url.startsWith('/static/')) return url
  if (url.startsWith('http')) return buildProxyMediaUrl(url)
  return url
}

function getXhsImageSrc(img: any): string {
  if (typeof img === 'string') {
    img = normalizeRemoteMediaUrl(img)
    if (img.startsWith('/static/')) return img
    return ''
  }
  if (img.local_path && img.local_path.startsWith('/static/')) return img.local_path
  return ''
}

function getLocalXhsImages(images: any[] = []) {
  return images.filter((img: any) => Boolean(getXhsImageSrc(img)))
}

function hasMissingXhsImages(images: any[] = []) {
  return images.some((img: any) => !getXhsImageSrc(img))
}

function getXhsVideoSrc(note: any): string {
  if (!note) return ''
  if (note.local_video_path && note.local_video_path.startsWith('/static/')) return note.local_video_path
  return ''
}

function onXhsVideoError(note: any) {
  note.video_error = '视频文件缺失或无法加载，请重新登录后重新抓取修复。'
}

function formatTime(t: string | null) {
  if (!t) return ''
  const d = new Date(t)
  return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(seconds: number) {
  if (!seconds) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}分${s.toString().padStart(2, '0')}秒` : `${s}秒`
}

onMounted(() => { realtime.connect(); loadAccounts(); loadLoginAccounts(); loadNotes() })

onUnmounted(() => {
  if (commentsRefreshTimer) {
    clearTimeout(commentsRefreshTimer)
  }
})
</script>

<style scoped>
.comment-ip {
  font-size: 12px;
  color: #999;
  margin-left: 8px;
}
.author-badge {
  margin-left: 4px;
  vertical-align: middle;
}
</style>
