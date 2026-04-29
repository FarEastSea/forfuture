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

    <div v-if="crawlStatus" class="crawl-status">
      <a-alert :type="crawlStatus.error ? 'error' : 'info'">
        {{ crawlStatus.error || crawlStatus.progress || '空闲' }}
      </a-alert>
    </div>

    <a-spin :loading="loading" tip="加载中..." style="width: 100%">
      <div v-if="notes.length === 0 && !loading" class="empty-state">
        <icon-empty :size="48" />
        <p>暂无笔记记录</p>
        <p class="sub-text">请先在设置中添加要监控的小红书账号，然后点击"抓取笔记"</p>
      </div>

      <div class="xhs-grid">
        <div v-for="note in notes" :key="note.id" class="note-card" @click="showDetail(note)">
          <div class="note-cover">
            <img v-if="getCover(note)" :src="getCover(note)" alt="" />
            <div v-else class="cover-placeholder"><icon-image :size="32" /></div>
            <div class="note-type-badge" v-if="note.video_url || note.note_type === 'video'">视频</div>
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
        <div class="detail-images" v-if="detailNote.images && detailNote.images.length">
          <a-image-preview-group>
            <a-image v-for="(img, i) in detailNote.images" :key="i" :src="getXhsImageSrc(img)" width="100%" fit="contain" />
          </a-image-preview-group>
        </div>
        <div class="detail-video" v-if="detailNote.local_video_path || detailNote.video_url">
          <video
            controls
            :src="detailNote.local_video_path || `/api/proxy/image?url=${encodeURIComponent(detailNote.video_url)}`"
            style="width: 100%; max-height: 400px; border-radius: 8px; background: #000"
          ></video>
        </div>
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
import { ref, onMounted } from 'vue'
import { xhsApi } from '@/api'
import { IconSync, IconHeart, IconStar, IconEmpty, IconImage, IconShareAlt, IconMessage } from '@arco-design/web-vue/es/icon'

const notes = ref<any[]>([])
const accounts = ref<any[]>([])
const selectedUid = ref('')
const loading = ref(false)
const crawling = ref(false)
const crawlMode = ref('incremental')
const crawlStatus = ref<any>(null)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const detailVisible = ref(false)
const detailNote = ref<any>(null)
const crawlingComments = ref(false)

async function loadNotes() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize }
    if (selectedUid.value) params.xhs_uid = selectedUid.value
    const { data } = await xhsApi.getNotes(params)
    notes.value = data.items || []
    total.value = data.total || 0
  } catch {} finally { loading.value = false }
}

async function loadAccounts() {
  try {
    const { data } = await xhsApi.getAccounts()
    accounts.value = data || []
  } catch {}
}

async function startCrawl() {
  const ids = accounts.value.map((a: any) => a.account_id)
  if (!ids.length) {
    const { Message } = await import('@arco-design/web-vue')
    Message.warning('请先在设置中添加要监控的小红书账号')
    return
  }
  crawling.value = true
  try {
    await xhsApi.crawl(ids, crawlMode.value)
    const interval = setInterval(async () => {
      try {
        const { data } = await xhsApi.getCrawlStatus()
        crawlStatus.value = data
        if (!data.running) { clearInterval(interval); crawling.value = false; loadNotes() }
      } catch { clearInterval(interval); crawling.value = false }
    }, 2000)
  } catch { crawling.value = false }
}

async function showDetail(note: any) {
  try {
    const { data } = await xhsApi.getNote(note.id)
    detailNote.value = data
    detailVisible.value = true
  } catch { detailNote.value = note; detailVisible.value = true }
}

async function crawlNoteComments(note: any) {
  if (!note?.id) return
  crawlingComments.value = true
  try {
    const { Message } = await import('@arco-design/web-vue')
    await xhsApi.crawlNoteComments(note.id)
    Message.info('评论抓取任务已启动，请稍后刷新查看')
    // 10秒后自动刷新详情
    setTimeout(async () => {
      try {
        const { data } = await xhsApi.getNote(note.id)
        detailNote.value = data
      } catch {}
      crawlingComments.value = false
    }, 15000)
  } catch {
    crawlingComments.value = false
  }
}

function getCover(note: any) {
  if (note.images && note.images.length) {
    const img = note.images[0]
    return img.local_path || img.url || img
  }
  return null
}

function getAvatarSrc(url: string): string {
  if (!url) return ''
  if (url.startsWith('/static/')) return url
  if (url.startsWith('http')) return `/api/proxy/image?url=${encodeURIComponent(url)}`
  return url
}

function getXhsImageSrc(img: any): string {
  if (typeof img === 'string') {
    if (img.startsWith('/static/') || img.startsWith('/api/')) return img
    if (img.startsWith('http')) return `/api/proxy/image?url=${encodeURIComponent(img)}`
    return img
  }
  if (img.local_path) return img.local_path
  if (img.url) return `/api/proxy/image?url=${encodeURIComponent(img.url)}`
  return ''
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

onMounted(() => { loadAccounts(); loadNotes() })
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
