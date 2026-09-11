<template>
  <div class="shell-backdrop" aria-hidden="true">
    <span class="backdrop-orb orb-tide"></span>
    <span class="backdrop-orb orb-dawn"></span>
    <span class="backdrop-grid"></span>
  </div>

  <a-layout class="app-layout">
    <a-layout-sider
      :width="sidebarWidth"
      :collapsed-width="sidebarCollapsedWidth"
      :collapsible="!isCompactViewport"
      :collapsed="sidebarCollapsed"
      @collapse="onCollapse"
      class="app-sider"
    >
      <div class="brand-panel" @click="router.push('/')">
        <div class="brand-mark">
          <icon-robot :size="24" />
        </div>
        <div v-if="!sidebarCollapsed" class="brand-copy">
          <p class="brand-kicker">Signal studio</p>
          <h1>AI Records</h1>
          <p>Capture, interpret, remind.</p>
        </div>
      </div>

      <div v-if="!sidebarCollapsed" class="sider-story">
        <span class="story-chip">{{ currentModule.badge }}</span>
        <h2>{{ currentModule.title }}</h2>
        <p>{{ currentModule.description }}</p>
        <div class="story-tags">
          <span v-for="highlight in currentModule.highlights" :key="highlight" class="story-tag">
            {{ highlight }}
          </span>
        </div>
      </div>

      <a-menu :selected-keys="[currentRoute]" @menu-item-click="onMenuClick" class="side-menu">
        <a-menu-item
          v-for="item in navItems"
          :key="item.key"
          :title="sidebarCollapsed ? item.label : undefined"
          :aria-label="item.label"
        >
          <template #icon>
            <component :is="item.icon" />
          </template>
          {{ item.label }}
        </a-menu-item>
      </a-menu>

      <div class="sider-footer">
        <a-popover trigger="click" position="rt" :content-style="{ padding: 0, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: '18px', minWidth: '300px' }">
          <div class="status-indicator" :class="[{ compact: sidebarCollapsed }, napcatConnected ? 'online' : 'offline']">
            <div class="status-dot" :class="napcatConnected ? 'online' : 'offline'"></div>
            <span v-if="!sidebarCollapsed" class="status-text">NapCat {{ napcatConnected ? '已连接' : '待接入' }}</span>
            <icon-info-circle class="status-icon" :class="{ compact: sidebarCollapsed }" :size="sidebarCollapsed ? 16 : 12" />
          </div>
          <template #content>
            <div class="napcat-popup">
              <div class="napcat-popup-header">
                <span>NapCat 连接状态</span>
                <a-button size="mini" type="text" @click="fetchNapcatStatus"><icon-refresh /></a-button>
              </div>
              <div class="napcat-popup-body">
                <div class="napcat-info-row">
                  <span class="napcat-label">插件连接</span>
                  <span :class="napcatConnected ? 'text-success' : 'text-error'">
                    {{ napcatConnected ? '在线' : '离线' }}
                    <span v-if="napcatQQ" class="napcat-inline-note">QQ {{ napcatQQ }}</span>
                  </span>
                </div>
                <div class="napcat-info-row">
                  <span class="napcat-label">前端 WS</span>
                  <span :class="wsStatus === 'connected' ? 'text-success' : 'text-error'">
                    {{ wsStatusText }}
                  </span>
                </div>
                <div class="napcat-info-row">
                  <span class="napcat-label">上次检查</span>
                  <span class="napcat-inline-note">{{ lastCheckTime || '未检查' }}</span>
                </div>
                <div v-if="napcatError" class="napcat-error-box">
                  {{ napcatError }}
                </div>
                <div v-if="!napcatConnected" class="napcat-help-box">
                  <p><b>排查步骤</b></p>
                  <ol>
                    <li>确认 NapCat 已启动且插件已启用</li>
                    <li>确认插件 backendWsUrl 地址正确</li>
                    <li class="napcat-inline-note">当前插件地址: {{ napcatWsInfo }}</li>
                    <li>检查后端服务和 WebSocket 代理是否正常</li>
                  </ol>
                </div>
              </div>
              <div class="napcat-popup-footer">
                <a-button size="small" type="text" @click="router.push('/settings')">
                  <icon-settings :size="14" /> 前往设置
                </a-button>
              </div>
            </div>
          </template>
        </a-popover>

        <div v-if="!sidebarCollapsed" class="footer-signals">
          <div class="mini-signal">
            <span class="mini-signal-label">WS</span>
            <strong>{{ wsStatusText }}</strong>
          </div>
          <div class="mini-signal secondary">
            <span class="mini-signal-label">Mode</span>
            <strong>{{ currentModule.badge }}</strong>
          </div>
        </div>

        <button type="button" class="theme-toggle" :class="{ compact: sidebarCollapsed }" @click="toggleTheme">
          <span class="theme-icon">
            <icon-moon-fill v-if="currentTheme === 'dark'" />
            <icon-sun-fill v-else-if="currentTheme === 'light'" />
            <icon-desktop v-else />
          </span>
          <span v-if="!sidebarCollapsed">{{ themeLabel }}</span>
        </button>
      </div>
    </a-layout-sider>

    <a-layout-content class="app-content">
      <div class="content-shell">
        <header class="workspace-header">
          <div class="workspace-copy">
            <div class="workspace-copy-main">
              <p class="workspace-kicker">{{ currentModule.badge }}</p>
              <h2>{{ currentModule.title }}</h2>
              <p>{{ currentModule.description }}</p>
            </div>

            <div class="workspace-copy-side">
              <div class="highlight-ribbon">
                <span v-for="highlight in currentModule.highlights" :key="highlight" class="highlight-pill">
                  {{ highlight }}
                </span>
              </div>
            </div>
          </div>
        </header>

        <div class="view-stage">
          <router-view />
        </div>
      </div>
    </a-layout-content>
  </a-layout>

  <a-modal
    :visible="adminTokenModalVisible"
    :mask-closable="false"
    :closable="false"
    :footer="false"
    width="420px"
  >
    <template #title>管理员认证</template>
    <div class="auth-modal-body">
      <p>管理接口已启用管理员令牌保护。请输入当前令牌后继续访问系统状态和实时 WebSocket。</p>
      <p class="auth-modal-note">未单独配置管理员令牌时，系统会回退使用服务端 SECRET_KEY。</p>
      <a-input-password
        v-model="adminTokenInput"
        allow-clear
        placeholder="请输入 ADMIN_API_TOKEN"
        @press-enter="submitAdminToken"
      />
      <div v-if="adminTokenHint" class="napcat-error-box">
        {{ adminTokenHint }}
      </div>
      <div class="auth-modal-actions">
        <a-button type="secondary" @click="clearAdminTokenAndRetry">清空令牌</a-button>
        <a-button type="primary" @click="submitAdminToken">保存并重连</a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  IconBook,
  IconClockCircle,
  IconDesktop,
  IconInfoCircle,
  IconMessage,
  IconMoonFill,
  IconRefresh,
  IconRobot,
  IconSearch,
  IconSettings,
  IconSunFill,
} from '@arco-design/web-vue/es/icon'
import axios from 'axios'
import { buildAdminAuthHeaders, clearStoredAdminToken, getStoredAdminToken, openAdminWebSocket, setStoredAdminToken } from '@/utils/adminToken'

type ThemeMode = 'light' | 'dark' | 'auto'

const navItems = [
  { key: 'qq', label: 'QQ空间', icon: IconMessage },
  { key: 'xhs', label: '小红书', icon: IconBook },
  { key: 'knowledge', label: '知识库', icon: IconSearch },
  { key: 'chat', label: 'AI对话', icon: IconRobot },
  { key: 'tasks', label: '定时任务', icon: IconClockCircle },
  { key: 'settings', label: '设置', icon: IconSettings },
]

const moduleMeta = {
  qq: {
    badge: 'Signal archive',
    title: 'QQ 空间观察站',
    description: '在时间轴上追踪动态、评论、媒体和设备线索，把碎片化记录整理成连续语境。',
    highlights: ['时间线', '评论上下文', '媒体痕迹'],
  },
  xhs: {
    badge: 'Narrative board',
    title: '小红书内容板',
    description: '以卡片流方式整理笔记、评论和标签，让内容表达与互动模式一眼成形。',
    highlights: ['笔记卡片', '评论关系', '标签聚类'],
  },
  knowledge: {
    badge: 'Memory index',
    title: '知识库检索',
    description: '对已采集记录做分块检索，按平台、作者和关键词把材料找回来。',
    highlights: ['双路检索', '结构化过滤', '增量索引'],
  },
  chat: {
    badge: 'Insight console',
    title: 'AI 语义工作台',
    description: '把已采集记录转成问答上下文，用对话方式做判断、解释和总结。',
    highlights: ['语义问答', '上下文模式', '流式响应'],
  },
  tasks: {
    badge: 'Orchestration lab',
    title: '提醒编排中心',
    description: '用自然语言定义触发条件，让系统按节奏检查记录并主动发出提醒。',
    highlights: ['任务编排', '定时执行', '触发日志'],
  },
  settings: {
    badge: 'Control room',
    title: '系统与接入配置',
    description: '统一管理 AI、账号、NapCat、数据库与日志，集中处理运行所需的关键开关。',
    highlights: ['账号管理', 'AI 配置', '系统状态'],
  },
}

const router = useRouter()
const route = useRoute()

const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
const compactQuery = window.matchMedia('(max-width: 1080px)')

const currentTheme = ref<ThemeMode>((localStorage.getItem('theme') as ThemeMode) || 'auto')
const isCompactViewport = ref(compactQuery.matches)
const manualCollapsed = ref(false)
const napcatConnected = ref(false)
const napcatQQ = ref('')
const napcatError = ref('')
const lastCheckTime = ref('')
const wsStatus = ref<'connected' | 'connecting' | 'disconnected'>('disconnected')
const adminTokenModalVisible = ref(false)
const adminTokenInput = ref(getStoredAdminToken())
const adminTokenHint = ref('')

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

const currentRoute = computed(() => route.path.split('/')[1] || 'qq')
const currentModule = computed(() => moduleMeta[currentRoute.value as keyof typeof moduleMeta] || moduleMeta.qq)
const sidebarCollapsed = computed(() => isCompactViewport.value || manualCollapsed.value)
const sidebarWidth = computed(() => (isCompactViewport.value ? 248 : 272))
const sidebarCollapsedWidth = computed(() => (isCompactViewport.value ? 78 : 88))
const napcatWsInfo = computed(() => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/napcat`)
const wsStatusText = computed(() => {
  if (wsStatus.value === 'connected') return '实时在线'
  if (wsStatus.value === 'connecting') return '重连中'
  return '已断开'
})
const themeLabel = computed(() => {
  if (currentTheme.value === 'dark') return '深色氛围'
  if (currentTheme.value === 'light') return '浅色纸面'
  return '跟随系统'
})

function getSystemTheme(): 'light' | 'dark' {
  return mediaQuery.matches ? 'dark' : 'light'
}

function applyTheme(mode: ThemeMode) {
  const resolvedTheme = mode === 'auto' ? getSystemTheme() : mode
  document.documentElement.classList.add('theme-transition')
  document.documentElement.setAttribute('data-theme', resolvedTheme)
  if (resolvedTheme === 'dark') {
    document.body.setAttribute('arco-theme', 'dark')
  } else {
    document.body.removeAttribute('arco-theme')
  }
  window.setTimeout(() => document.documentElement.classList.remove('theme-transition'), 240)
}

function toggleTheme() {
  const modes: ThemeMode[] = ['auto', 'light', 'dark']
  const index = modes.indexOf(currentTheme.value)
  currentTheme.value = modes[(index + 1) % modes.length]
  localStorage.setItem('theme', currentTheme.value)
  applyTheme(currentTheme.value)
}

function onSystemThemeChange() {
  if (currentTheme.value === 'auto') {
    applyTheme('auto')
  }
}

function onViewportChange(event: MediaQueryList | MediaQueryListEvent) {
  isCompactViewport.value = event.matches
  manualCollapsed.value = false
}

function onCollapse(value: boolean) {
  if (isCompactViewport.value) return
  manualCollapsed.value = value
}

function onMenuClick(key: string) {
  router.push(`/${key}`)
}

function openAdminTokenModal(message: string, resetStoredToken: boolean = false) {
  if (resetStoredToken) {
    clearStoredAdminToken()
  }
  adminTokenInput.value = getStoredAdminToken()
  adminTokenHint.value = message
  adminTokenModalVisible.value = true
}

function clearAdminTokenAndRetry() {
  clearStoredAdminToken()
  adminTokenInput.value = ''
  adminTokenHint.value = '请输入新的管理员令牌。'
}

function submitAdminToken() {
  const token = adminTokenInput.value.trim()
  if (!token) {
    adminTokenHint.value = '管理员令牌不能为空。'
    return
  }

  setStoredAdminToken(token)
  adminTokenModalVisible.value = false
  adminTokenHint.value = ''
  napcatError.value = ''
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  void fetchNapcatStatus()
  connectWS()
}

async function fetchNapcatStatus() {
  if (adminTokenModalVisible.value && !getStoredAdminToken()) {
    return
  }

  try {
    const { data } = await axios.get('/api/v2/system/napcat-status', {
      headers: buildAdminAuthHeaders(),
    })
    lastCheckTime.value = new Date().toLocaleTimeString('zh-CN')
    napcatError.value = ''
    adminTokenModalVisible.value = false
    if (data.connected_count > 0) {
      napcatConnected.value = true
      napcatQQ.value = data.connections?.[0]?.qq || ''
    } else {
      napcatConnected.value = false
      napcatQQ.value = ''
    }
  } catch (error: any) {
    lastCheckTime.value = new Date().toLocaleTimeString('zh-CN')
    const status = error.response?.status
    const detail = error.response?.data?.detail || ''
    if (status === 401) {
      openAdminTokenModal(detail || '管理员令牌缺失或无效。', true)
      napcatError.value = '管理员认证失败，请重新输入令牌。'
    } else if (status === 503) {
      adminTokenModalVisible.value = false
      napcatError.value = detail || '服务器尚未配置管理员令牌；请在服务端设置 ADMIN_API_TOKEN，或使用非默认 SECRET_KEY 作为回退令牌。'
    } else {
      napcatError.value = `后端请求失败: ${detail || error.message || '未知错误'}`
    }
    napcatConnected.value = false
  }
}

function connectWS() {
  const storedToken = getStoredAdminToken()
  if (!storedToken) {
    wsStatus.value = 'disconnected'
    return
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close()
  }
  wsStatus.value = 'connecting'
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = openAdminWebSocket(`${protocol}//${location.host}/ws/v2/events`)

  ws.onopen = () => {
    wsStatus.value = 'connected'
    adminTokenModalVisible.value = false
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'napcat.status') {
        const status = data.payload || {}
        napcatConnected.value = Number(status.connected_count || 0) > 0
        napcatQQ.value = status.connections?.[0]?.qq || ''
        lastCheckTime.value = new Date().toLocaleTimeString('zh-CN')
      }
    } catch {
      // ignore non-json messages
    }
  }

  ws.onclose = (event) => {
    wsStatus.value = 'disconnected'
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (event.code === 1008) {
      const reason = event.reason || '管理员认证失败。'
      if (reason.includes('未配置')) {
        napcatError.value = reason
        adminTokenModalVisible.value = false
      } else {
        openAdminTokenModal(reason, true)
        napcatError.value = reason
      }
      return
    }
    reconnectTimer = setTimeout(connectWS, 5000)
  }

  ws.onerror = () => ws?.close()
}

const handleRefreshStatus = () => {
  void fetchNapcatStatus()
}

onMounted(() => {
  applyTheme(currentTheme.value)
  onViewportChange(compactQuery)
  void fetchNapcatStatus()
  connectWS()
  window.addEventListener('refresh-napcat-status', handleRefreshStatus)
  mediaQuery.addEventListener('change', onSystemThemeChange)
  compactQuery.addEventListener('change', onViewportChange)
})

onUnmounted(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  window.removeEventListener('refresh-napcat-status', handleRefreshStatus)
  mediaQuery.removeEventListener('change', onSystemThemeChange)
  compactQuery.removeEventListener('change', onViewportChange)
  ws?.close()
})
</script>
