import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { v2Api } from '@/api/v2'
import { openAdminWebSocket } from '@/utils/adminToken'
import { Message } from '@arco-design/web-vue'

export function useSettingsView() {
  const settingKeyMap: Record<string, string> = {
    auto_crawl_interval: 'auto_crawl_interval_minutes',
    cookie_failure_threshold: 'credential_failure_threshold',
    risk_cooldown_minutes: 'credential_cooldown_minutes',
    xhs_crawl_detail_delay_seconds: 'xhs_detail_delay_seconds',
    xhs_profile_scroll_delay_seconds: 'xhs_scroll_delay_seconds',
    ai_vision_enabled: 'kb_vision_enabled',
  }
  const reverseSettingKeyMap = Object.fromEntries(Object.entries(settingKeyMap).map(([oldKey, newKey]) => [newKey, oldKey]))
  const systemApi = {
    async getConfigs() {
      const response = await v2Api.readSettings()
      const items = Object.entries(response.data.values || {}).map(([key, value]) => ({
        key: reverseSettingKeyMap[key] || key,
        value: typeof value === 'boolean' ? String(value) : String(value ?? ''),
      }))
      return { ...response, data: items }
    },
    async updateConfigs(items: Array<{ key: string; value: unknown }>) {
      const infrastructure = items.filter((item) => ['database_host', 'database_port', 'database_name', 'database_user', 'database_password', 'napcat_ws_url', 'napcat_token', 'redis_url', 'redis_enabled'].includes(item.key))
      const runtime = items.filter((item) => !infrastructure.includes(item))
      if (infrastructure.length) {
        await v2Api.updateInfrastructure(Object.fromEntries(infrastructure.map((item) => [item.key, String(item.value ?? '')])))
      }
      if (!runtime.length) return { data: { message: '配置已更新' } }
      const values = Object.fromEntries(runtime.map((item) => [settingKeyMap[item.key] || item.key, item.value]))
      return v2Api.updateSettings(values)
    },
    getDbConfig: v2Api.readDatabaseConfig,
    testDbConnection: v2Api.testDatabaseConnection,
    getLogs: v2Api.readLogs,
    clearLogs: v2Api.clearLogs,
    getSummaryStats: v2Api.readSummaryStats,
    generateSummaries: v2Api.generateSummaries,
  }

  // 账号管理
  const accounts = ref<any[]>([])
  const monitoredAccounts = computed(() => accounts.value.filter(a => a.is_target === 1))
  const loginAccounts = computed(() => accounts.value.filter(a => a.is_target === 0 || a.is_target === null))
  const monitoredQQAccounts = computed(() => monitoredAccounts.value.filter(a => a.platform === 'qq'))
  const monitoredXHSAccounts = computed(() => monitoredAccounts.value.filter(a => a.platform === 'xhs'))
  const loginQQAccounts = computed(() => loginAccounts.value.filter(a => a.platform === 'qq'))
  const loginXHSAccounts = computed(() => loginAccounts.value.filter(a => a.platform === 'xhs'))
  const activeAccountCount = computed(() => accounts.value.filter(a => a.status === 'active').length)
  const elevatedLoginRiskCount = computed(() => loginAccounts.value.filter(a => ['degraded', 'relogin_pending', 'expired'].includes(normalizeAccountStatus(a.status))).length)
  const cooldownLoginCount = computed(() => loginAccounts.value.filter(a => Boolean(a.is_in_cooldown)).length)
  const accountModalVisible = ref(false)
  const accountForm = reactive({ platform: 'qq', account_id: '', nickname: '' })
  const refreshingId = ref<number | null>(null)
  const refreshingAll = ref(false)

  // AI配置
  const aiConfigs = ref<any[]>([])
  const configModalVisible = ref(false)
  const configSaving = ref(false)
  const editingConfig = ref<any>(null)
  const configForm = reactive({
    name: '', api_base: '', api_key: '', model: '',
    embed_model: '', max_tokens: 4096, temperature: 0.7,
  })
  const aiTesting = ref(false)
  const aiTestResult = ref<any>(null)

  // 数据库
  const dbForm = reactive({ host: '', port: 54320, name: '', user: '', password: '' })
  const dbTesting = ref(false)
  const dbTestResult = ref<any>(null)

  // Redis
  const redisForm = reactive({ enabled: true, url: '', displayUrl: '' })
  const redisTesting = ref(false)
  const redisSaving = ref(false)
  const redisTestResult = ref<any>(null)

  // NapCat
  const napcatUrl = ref('ws://127.0.0.1:3001')
  const napcatToken = ref('')

  // 日志系统
  const logs = ref<any[]>([])
  const logsLoading = ref(false)
  const logLevel = ref<string>('')
  const logKeyword = ref('')
  const logModule = ref<string>('')
  const logAutoRefresh = ref(false)
  const logTotalBuffered = ref(0)
  let logRefreshTimer: ReturnType<typeof setInterval> | null = null

  // 自动爬取
  const autoCrawlEnabled = ref(true)
  const autoCrawlInterval = ref(60)
  const skipCrawlWhenLoginDegraded = ref(true)
  const cookieFailureThreshold = ref(2)
  const riskCooldownMinutes = ref(30)
  const xhsCrawlDetailDelaySeconds = ref(5)
  const xhsProfileScrollDelaySeconds = ref(3)

  // AI上下文模式
  const aiContextMode = ref('full')
  const summaryStats = ref<any>(null)
  const summaryLoading = ref(false)
  const summaryGenerating = ref(false)
  const autoSummaryEnabled = ref(true)
  const aiVisionEnabled = ref(false)
  const serverBaseUrl = ref('')

  // 通知
  const loginNotifyEnabled = ref(false)

  // 管理员配置
  const adminForm = reactive({
    admin_qq: '',
    admin_name: '',
    allow_send_to_monitored: false,
  })
  const adminSaving = ref(false)

  // 扫码
  const qrcodeData = ref('')
  const xhsLogging = ref(false)
  const qqLogging = ref(false)
  const cookieRefreshing = ref(false)
  const napcatLogging = ref(false)
  const qrcodeLoginType = ref('')  // 'qq' | 'xhs'
  const loginStatusText = ref('')
  const loginStatusColor = ref('')
  let loginPollInterval: ReturnType<typeof setInterval> | null = null

  const loginStatusToneClass = computed(() => {
    if (loginStatusColor.value === 'var(--text-muted)') return 'qrcode-status-muted'
    if (loginStatusColor.value === 'var(--color-warning-6)') return 'qrcode-status-warning'
    if (loginStatusColor.value === 'var(--color-danger-6)') return 'qrcode-status-danger'
    return ''
  })

  // 浏览器实时预览 (WebSocket)
  const browserPreviewActive = ref(false)
  const browserPreviewConnected = ref(false)
  const browserPreviewFrame = ref<any>(null)
  const browserPreviewEvents = ref<any[]>([])
  const previewFps = ref(0)
  const previewInputText = ref('')
  const previewAddressUrl = ref('')
  const addressBarFocused = ref(false)
  const savingCookies = ref(false)
  const resettingBrowser = ref(false)
  let previewWs: WebSocket | null = null
  let previewFrameCount = 0
  let previewFpsTimer: ReturnType<typeof setInterval> | null = null
  const keepPreviewAfterLogin = ref(false)
  const previewState = computed(() => {
    if (browserPreviewFrame.value?.screenshot) return 'ready'
    if (browserPreviewConnected.value && browserPreviewFrame.value) return 'booting'
    if (browserPreviewConnected.value) return 'starting'
    if (browserPreviewActive.value) return 'connecting'
    if (qrcodeLoginType.value) return 'idle'
    return 'hidden'
  })

  function getErrorMessage(error: unknown, fallback: string) {
    if (typeof error === 'object' && error && 'response' in error) {
      const response = (error as any).response
      const detail = response?.data?.detail
      if (typeof detail === 'string' && detail) return detail
      if (response?.status === 502) return '后端服务暂时不可用（502），请稍后重试'
      if (response?.status === 503) return '服务正在启动或依赖不可用（503），请稍后重试'
    }
    if (error instanceof Error && error.message) return error.message
    return fallback
  }

  function normalizeAccountStatus(status?: string) {
    return (status || 'active').trim().toLowerCase()
  }

  function getAccountStatusBadge(account: any, mode: 'monitor' | 'login' = 'monitor') {
    const normalizedStatus = normalizeAccountStatus(account.status)

    if (normalizedStatus === 'disabled') {
      return { badgeStatus: 'normal', text: '已禁用' }
    }
    if (normalizedStatus === 'degraded') {
      return { badgeStatus: 'warning', text: mode === 'login' ? '降级' : '已启用' }
    }
    if (normalizedStatus === 'relogin_pending') {
      return { badgeStatus: 'danger', text: mode === 'login' ? '待重登' : '待处理' }
    }
    if (normalizedStatus === 'expired') {
      return { badgeStatus: 'danger', text: mode === 'login' ? '已过期' : '异常' }
    }

    return { badgeStatus: 'success', text: mode === 'login' ? '有效' : '已启用' }
  }

  function getAccountToggleType(account: any) {
    return normalizeAccountStatus(account.status) === 'disabled' ? 'primary' : 'secondary'
  }

  function getAccountToggleLabel(account: any) {
    return normalizeAccountStatus(account.status) === 'disabled' ? '启用' : '禁用'
  }

  function formatShortDateTime(value?: string | null) {
    if (!value) return '-'
    return value.replace('T', ' ').slice(0, 16)
  }

  function shortenRiskReason(reason?: string | null) {
    if (!reason) return ''
    return reason.length > 26 ? `${reason.slice(0, 26)}...` : reason
  }

  function formatRiskSummary(account: any) {
    const normalizedStatus = normalizeAccountStatus(account.status)

    if (account.is_in_cooldown && account.risk_cooldown_until) {
      return `冷却至 ${formatShortDateTime(account.risk_cooldown_until)}`
    }
    if (normalizedStatus === 'relogin_pending') {
      return account.last_failure_reason ? `待重新登录 · ${shortenRiskReason(account.last_failure_reason)}` : '待重新登录'
    }
    if (normalizedStatus === 'degraded') {
      return account.last_failure_reason ? `降级运行 · ${shortenRiskReason(account.last_failure_reason)}` : '降级运行'
    }
    if (normalizedStatus === 'expired') {
      return account.last_failure_reason ? `Cookie 已过期 · ${shortenRiskReason(account.last_failure_reason)}` : 'Cookie 已过期'
    }
    if (account.failure_count) {
      return account.last_failure_reason ? `累计失败 ${account.failure_count} 次 · ${shortenRiskReason(account.last_failure_reason)}` : `累计失败 ${account.failure_count} 次`
    }
    if (account.cookie_last_validated_at) {
      return `最近验证 ${formatShortDateTime(account.cookie_last_validated_at)}`
    }
    return '尚未记录 Cookie 验证'
  }

  async function loadAccounts() {
    try {
      const { data } = await v2Api.listAllAccounts()
      accounts.value = data.items || []
    } catch (e: any) {
      Message.error(getErrorMessage(e, '账号列表加载失败'))
    }
  }

  async function loadAIConfigs() {
    try {
      const { data } = await v2Api.listProviders()
      aiConfigs.value = data.items || []
    } catch (e: any) {
      Message.error(getErrorMessage(e, 'AI 配置加载失败'))
    }
  }

  async function loadDbConfig() {
    try {
      const { data } = await systemApi.getDbConfig()
      Object.assign(dbForm, data)
    } catch (e: any) {
      Message.error(getErrorMessage(e, '数据库配置加载失败'))
    }
  }

  async function loadRedisConfig() {
    try {
      const { data } = await v2Api.readRedisConfig()
      redisForm.enabled = Boolean(data.enabled)
      redisForm.displayUrl = data.display_url || ''
      redisForm.url = ''
    } catch (e: any) {
      Message.error(getErrorMessage(e, 'Redis 配置加载失败'))
    }
  }

  function showAddAccount() {
    Object.assign(accountForm, { platform: 'qq', account_id: '', nickname: '' })
    accountModalVisible.value = true
  }

  async function addAccount() {
    if (!accountForm.account_id) { Message.warning('请输入账号ID'); return }
    try {
      await v2Api.createTarget({ ...accountForm })
      Message.success('添加成功')
      accountModalVisible.value = false
      loadAccounts()
    } catch (e: any) { Message.error(e.response?.data?.detail || '添加失败') }
  }

  async function removeAccount(account: any) {
    try {
      if (account.is_target === 1) await v2Api.deleteTarget(account.id)
      else await v2Api.deleteCredential(account.id)
      Message.success('账号已删除')
      loadAccounts()
    } catch (e: any) {
      Message.error(getErrorMessage(e, '删除账号失败'))
    }
  }

  async function toggleAccount(account: any) {
    try {
      const { data } = account.is_target === 1
        ? await v2Api.toggleTarget(account.id)
        : await v2Api.toggleCredential(account.id)
      Message.success(data.message || '操作成功')
      loadAccounts()
    } catch (e: any) { Message.error(e.response?.data?.detail || '操作失败') }
  }

  async function refreshAccountInfo(id: number) {
    refreshingId.value = id
    try {
      const { data } = await v2Api.refreshTarget(id)
      Message.success(data.message || '刷新成功')
      loadAccounts()
    } catch (e: any) {
      Message.error(e.response?.data?.detail || '刷新失败，请先登录对应平台')
    } finally {
      refreshingId.value = null
    }
  }

  async function refreshAllAccountsAction() {
    refreshingAll.value = true
    try {
      const { data } = await v2Api.refreshAllTargets()
      Message.success(data.message || '刷新完成')
      await loadAccounts()
    } catch (e: any) {
      Message.error(e.response?.data?.detail || '批量刷新失败')
    } finally {
      refreshingAll.value = false
    }
  }

  function getAvatarSrc(url: string | undefined): string {
    if (!url) return ''
    // 本地已下载的路径
    if (url.startsWith('/static/')) return url
    // 远程URL用代理
    if (url.startsWith('http')) return `/api/v2/media/proxy?url=${encodeURIComponent(url)}`
    return url
  }

  function getAvatarCdnFallback(record: any): string {
    // QQ头像CDN回退（仅QQ平台有效）
    if (typeof record === 'string') {
      // 兼容旧调用方式（传account_id字符串）
      return `/api/v2/media/proxy?url=${encodeURIComponent(`https://q.qlogo.cn/headimg_dl?dst_uin=${record}&spec=640&img_type=jpg`)}`
    }
    if (record?.platform === 'qq') {
      return `/api/v2/media/proxy?url=${encodeURIComponent(`https://q.qlogo.cn/headimg_dl?dst_uin=${record.account_id}&spec=640&img_type=jpg`)}`
    }
    return ''
  }

  function showAddConfig() {
    editingConfig.value = null
    Object.assign(configForm, { name: '', api_base: '', api_key: '', model: '', embed_model: '', max_tokens: 4096, temperature: 0.7 })
    aiTestResult.value = null
    configModalVisible.value = true
  }

  function editConfig(config: any) {
    editingConfig.value = config
    Object.assign(configForm, {
      name: config.name, api_base: config.api_base,
      api_key: '',
      model: config.model, embed_model: config.embed_model || '',
      max_tokens: config.max_tokens, temperature: config.temperature,
    })
    aiTestResult.value = null
    configModalVisible.value = true
  }

  async function saveConfig() {
    if (!configForm.name || !configForm.api_base || !configForm.model || (!editingConfig.value && !configForm.api_key)) {
      Message.warning('请填写必填项'); return
    }
    configSaving.value = true
    try {
      if (editingConfig.value) {
        await v2Api.updateProvider(editingConfig.value.id, { ...configForm, temperature: Number(configForm.temperature) })
      } else {
        await v2Api.createProvider({ ...configForm, temperature: Number(configForm.temperature) })
      }
      Message.success('保存成功')
      configModalVisible.value = false
      loadAIConfigs()
    } catch (e: any) { Message.error('保存失败') }
    finally { configSaving.value = false }
  }

  async function activateConfig(id: number) {
    try {
      await v2Api.activateProvider(id)
      Message.success('已激活')
      loadAIConfigs()
    } catch (e: any) {
      Message.error(getErrorMessage(e, '激活配置失败'))
    }
  }

  async function deleteConfig(id: number) {
    try {
      await v2Api.deleteProvider(id)
      Message.success('配置已删除')
      loadAIConfigs()
    } catch (e: any) {
      Message.error(getErrorMessage(e, '删除配置失败'))
    }
  }

  async function testAIConfig() {
    aiTesting.value = true
    aiTestResult.value = null
    try {
      const { data } = await v2Api.testProvider({ ...configForm, temperature: Number(configForm.temperature) })
      aiTestResult.value = data
    } catch (e: any) {
      aiTestResult.value = { success: false, message: '连接失败' }
    } finally { aiTesting.value = false }
  }

  async function testDbConnection() {
    dbTesting.value = true
    dbTestResult.value = null
    try {
      const { data } = await systemApi.testDbConnection(dbForm)
      dbTestResult.value = data
    } catch (e: any) {
      dbTestResult.value = { success: false, message: '连接失败' }
    } finally { dbTesting.value = false }
  }

  async function saveDbConfig() {
    try {
      await systemApi.updateConfigs([
        { key: 'database_host', value: dbForm.host },
        { key: 'database_port', value: String(dbForm.port) },
        { key: 'database_name', value: dbForm.name },
        { key: 'database_user', value: dbForm.user },
        { key: 'database_password', value: dbForm.password },
      ])
      Message.success('数据库配置已保存（重启后端生效）')
    } catch (e: any) {
      console.error('保存数据库配置失败:', e)
      Message.error(e.response?.data?.detail || '保存失败，请检查后端日志')
    }
  }

  async function testRedisConnection() {
    redisTesting.value = true
    redisTestResult.value = null
    try {
      const { data } = await v2Api.testRedisConnection(redisForm.url)
      redisTestResult.value = data
    } catch (e: any) {
      redisTestResult.value = { success: false, message: getErrorMessage(e, 'Redis 连接测试失败') }
    } finally {
      redisTesting.value = false
    }
  }

  async function saveRedisConfig() {
    if (redisForm.enabled && redisForm.url && !/^rediss?:\/\//i.test(redisForm.url)) {
      Message.warning('Redis 地址必须以 redis:// 或 rediss:// 开头')
      return
    }
    redisSaving.value = true
    try {
      const values: Record<string, string> = { redis_enabled: redisForm.enabled ? 'true' : 'false' }
      if (redisForm.url.trim()) values.redis_url = redisForm.url.trim()
      await v2Api.updateInfrastructure(values)
      Message.success('Redis 配置已保存，重启后端后生效')
      await loadRedisConfig()
    } catch (e: any) {
      Message.error(getErrorMessage(e, 'Redis 配置保存失败'))
    } finally {
      redisSaving.value = false
    }
  }

  async function saveNapcatConfig() {
    try {
      await systemApi.updateConfigs([
        { key: 'napcat_ws_url', value: napcatUrl.value },
        { key: 'napcat_token', value: napcatToken.value },
      ])
      Message.success('NapCat配置已保存')
      window.dispatchEvent(new Event('refresh-napcat-status'))
    } catch (e: any) {
      console.error('保存NapCat配置失败:', e)
      Message.error(e.response?.data?.detail || '保存失败，请检查后端日志')
    }
  }

  async function saveLoginNotifyConfig() {
    try {
      await systemApi.updateConfigs([
        { key: 'login_notify_enabled', value: loginNotifyEnabled.value ? 'true' : 'false' },
      ])
      Message.success(loginNotifyEnabled.value ? '已开启Cookie过期通知' : '已关闭Cookie过期通知')
    } catch (e: any) {
      Message.error('保存失败')
    }
  }

  async function saveAutoCrawlConfig() {
    try {
      await systemApi.updateConfigs([
        { key: 'auto_crawl_enabled', value: autoCrawlEnabled.value ? 'true' : 'false' },
        { key: 'auto_crawl_interval', value: String(autoCrawlInterval.value) },
      ])
      Message.success('自动爬取配置已保存（重启后端生效）')
    } catch (e: any) {
      console.error('保存自动爬取配置失败:', e)
      Message.error(e.response?.data?.detail || '保存失败，请检查后端日志')
    }
  }

  async function saveRiskControlConfig() {
    if (cookieFailureThreshold.value < 1) {
      Message.warning('失败阈值至少为 1')
      return
    }
    if (riskCooldownMinutes.value < 0) {
      Message.warning('冷却时间不能小于 0')
      return
    }

    try {
      await systemApi.updateConfigs([
        { key: 'skip_crawl_when_login_degraded', value: skipCrawlWhenLoginDegraded.value ? 'true' : 'false' },
        { key: 'cookie_failure_threshold', value: String(cookieFailureThreshold.value) },
        { key: 'risk_cooldown_minutes', value: String(riskCooldownMinutes.value) },
      ])
      Message.success('登录风控配置已保存')
    } catch (e: any) {
      Message.error(getErrorMessage(e, '保存风控配置失败'))
    }
  }

  async function saveXhsCrawlTimingConfig() {
    if (xhsCrawlDetailDelaySeconds.value < 3) {
      Message.warning('详情抓取间隔至少为 3 秒')
      return
    }
    if (xhsProfileScrollDelaySeconds.value < 2) {
      Message.warning('主页滚动间隔至少为 2 秒')
      return
    }

    try {
      await systemApi.updateConfigs([
        { key: 'xhs_crawl_detail_delay_seconds', value: String(xhsCrawlDetailDelaySeconds.value) },
        { key: 'xhs_profile_scroll_delay_seconds', value: String(xhsProfileScrollDelaySeconds.value) },
      ])
      Message.success('小红书抓取节奏已保存')
    } catch (e: any) {
      Message.error(getErrorMessage(e, '保存小红书抓取节奏失败'))
    }
  }

  async function saveAutoSummaryEnabled() {
    try {
      await systemApi.updateConfigs([
        { key: 'auto_summary_enabled', value: autoSummaryEnabled.value ? 'true' : 'false' },
      ])
      Message.success(autoSummaryEnabled.value ? '已开启每周自动总结' : '已关闭每周自动总结')
    } catch (e: any) {
      Message.error('保存失败')
    }
  }

  async function saveVisionConfig() {
    try {
      await systemApi.updateConfigs([
        { key: 'ai_vision_enabled', value: aiVisionEnabled.value ? 'true' : 'false' },
        { key: 'server_base_url', value: serverBaseUrl.value },
      ])
      if (aiVisionEnabled.value) {
        Message.success('图像理解已开启')
      } else {
        Message.info('图像理解已关闭')
      }
    } catch (e: any) {
      Message.error('保存失败')
    }
  }

  async function saveAIContextMode() {
    try {
      await systemApi.updateConfigs([
        { key: 'ai_context_mode', value: aiContextMode.value },
      ])
      const labels: Record<string, string> = {
        full: '全量模式', auto: '自动模式',
        smart_summary: '总结模式', smart_search: '搜索模式',
      }
      Message.success(`AI上下文已切换为${labels[aiContextMode.value] || aiContextMode.value}`)
    } catch (e: any) {
      Message.error('保存失败')
    }
  }

  async function loadSummaryStats() {
    summaryLoading.value = true
    try {
      const { data } = await systemApi.getSummaryStats()
      summaryStats.value = data
    } catch (e: any) {
      Message.error('获取总结统计失败')
    } finally {
      summaryLoading.value = false
    }
  }

  async function generateSummaries(forceAll: boolean) {
    summaryGenerating.value = true
    try {
      const { data } = await systemApi.generateSummaries(forceAll)
      Message.success(data.message || '总结完成')
      await loadSummaryStats()
    } catch (e: any) {
      Message.error(e.response?.data?.detail || '总结生成失败')
    } finally {
      summaryGenerating.value = false
    }
  }

  async function loadSystemConfigs() {
    try {
      const { data } = await systemApi.getConfigs()
      for (const c of data) {
        if (c.key === 'auto_crawl_enabled') autoCrawlEnabled.value = c.value !== 'false'
        if (c.key === 'auto_crawl_interval') autoCrawlInterval.value = parseInt(c.value) || 60
        if (c.key === 'skip_crawl_when_login_degraded') skipCrawlWhenLoginDegraded.value = c.value !== 'false'
        if (c.key === 'cookie_failure_threshold') cookieFailureThreshold.value = parseInt(c.value) || 2
        if (c.key === 'risk_cooldown_minutes') riskCooldownMinutes.value = parseInt(c.value) || 0
        if (c.key === 'xhs_crawl_detail_delay_seconds') xhsCrawlDetailDelaySeconds.value = Number(c.value) || 5
        if (c.key === 'xhs_profile_scroll_delay_seconds') xhsProfileScrollDelaySeconds.value = Number(c.value) || 3
        if (c.key === 'napcat_ws_url' && c.value) napcatUrl.value = c.value
        if (c.key === 'napcat_token' && c.value) napcatToken.value = c.value
        if (c.key === 'login_notify_enabled') loginNotifyEnabled.value = c.value === 'true'
        if (c.key === 'ai_context_mode' && c.value) aiContextMode.value = c.value
        if (c.key === 'auto_summary_enabled') autoSummaryEnabled.value = c.value !== 'false'
        if (c.key === 'ai_vision_enabled') aiVisionEnabled.value = c.value === 'true'
        if (c.key === 'server_base_url' && c.value) serverBaseUrl.value = c.value
        // 管理员配置
        if (c.key === 'admin_qq') adminForm.admin_qq = c.value || ''
        if (c.key === 'admin_name') adminForm.admin_name = c.value || ''
        if (c.key === 'allow_send_to_monitored') adminForm.allow_send_to_monitored = c.value === 'true'
      }
    } catch (e) {
      console.error('加载系统配置失败:', e)
    }
  }

  async function saveAdminConfig() {
    if (!adminForm.admin_qq) {
      Message.warning('请输入管理员QQ号')
      return
    }
    adminSaving.value = true
    try {
      await systemApi.updateConfigs([
        { key: 'admin_qq', value: adminForm.admin_qq },
        { key: 'admin_name', value: adminForm.admin_name },
      ])
      Message.success('管理员配置已保存')
    } catch (e: any) {
      Message.error(e.response?.data?.detail || '保存失败')
    } finally {
      adminSaving.value = false
    }
  }

  async function saveAdminSafety() {
    try {
      await systemApi.updateConfigs([
        { key: 'allow_send_to_monitored', value: adminForm.allow_send_to_monitored ? 'true' : 'false' },
      ])
      Message.success(adminForm.allow_send_to_monitored ? '已允许向被监控账号发消息（不推荐）' : '已禁止向被监控账号发消息')
    } catch (e: any) {
      Message.error('保存失败')
    }
  }

  async function loadLogs() {
    logsLoading.value = true
    try {
      const params: any = { limit: 300 }
      if (logLevel.value) params.level = logLevel.value
      if (logModule.value) params.module = logModule.value
      if (logKeyword.value) params.keyword = logKeyword.value
      const { data } = await systemApi.getLogs(params)
      logs.value = data.logs || []
      logTotalBuffered.value = data.total_buffered || 0
    } catch (e) {
      Message.error('获取日志失败')
    } finally {
      logsLoading.value = false
    }
  }

  async function clearLogs() {
    try {
      await systemApi.clearLogs()
      logs.value = []
      logTotalBuffered.value = 0
      Message.success('日志已清空')
    } catch {}
  }

  async function copyLogs() {
    if (logs.value.length === 0) {
      Message.warning('暂无日志可复制')
      return
    }
    const text = logs.value.map(l =>
      `${l.time?.replace('T', ' ').slice(11, 19)} [${l.level?.toUpperCase()}] [${l.logger || ''}] ${l.message}`
    ).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      Message.success(`已复制 ${logs.value.length} 条日志`)
    } catch {
      // fallback
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      Message.success(`已复制 ${logs.value.length} 条日志`)
    }
  }

  function toggleAutoRefresh(val: string | number | boolean) {
    if (val) {
      loadLogs()
      logRefreshTimer = setInterval(loadLogs, 3000)
    } else {
      if (logRefreshTimer) { clearInterval(logRefreshTimer); logRefreshTimer = null }
    }
  }

  function cleanupLoginPoll() {
    if (loginPollInterval) { clearInterval(loginPollInterval); loginPollInterval = null }
  }

  function startBrowserPreview(platform: string) {
    stopBrowserPreview()
    browserPreviewActive.value = true
    browserPreviewEvents.value = []
    browserPreviewFrame.value = null
    previewFrameCount = 0
    previewFps.value = 0

    // 计算 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/browser-preview`
    previewWs = openAdminWebSocket(wsUrl)

    previewWs.onopen = () => {
      browserPreviewConnected.value = true
      previewWs?.send(JSON.stringify({ platform }))
      // FPS 计数器
      previewFpsTimer = setInterval(() => {
        previewFps.value = previewFrameCount
        previewFrameCount = 0
      }, 1000)
      addPreviewEvent('WebSocket 已连接')
    }

    previewWs.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'frame') {
          browserPreviewFrame.value = msg
          previewFrameCount++
          // 地址栏未聚焦时自动同步URL
          if (!addressBarFocused.value && msg.url) {
            previewAddressUrl.value = msg.url
          }
        } else if (msg.type === 'status') {
          browserPreviewFrame.value = { ...browserPreviewFrame.value, ...msg, screenshot: browserPreviewFrame.value?.screenshot || '' }
        } else if (msg.type === 'navigate_result') {
          if (msg.ok) {
            addPreviewEvent(`导航成功: ${msg.url || ''}`)
          } else {
            addPreviewEvent(`导航失败: ${msg.error || ''}`)
          }
        } else if (msg.type === 'closed') {
          addPreviewEvent(`浏览器已关闭: ${msg.reason || ''}`)
        } else if (msg.type === 'error') {
          addPreviewEvent(`错误: ${msg.message || ''}`)
        }
      } catch {}
    }

    previewWs.onclose = () => {
      browserPreviewConnected.value = false
      browserPreviewActive.value = false
      addPreviewEvent('WebSocket 已断开')
      if (previewFpsTimer) { clearInterval(previewFpsTimer); previewFpsTimer = null }
    }

    previewWs.onerror = () => {
      browserPreviewActive.value = false
      addPreviewEvent('WebSocket 连接错误')
    }
  }

  function stopBrowserPreview() {
    if (previewWs) {
      try { previewWs.close() } catch {}
      previewWs = null
    }
    browserPreviewConnected.value = false
    browserPreviewActive.value = false
    if (previewFpsTimer) { clearInterval(previewFpsTimer); previewFpsTimer = null }
  }

  function addPreviewEvent(msg: string) {
    const now = new Date()
    const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`
    browserPreviewEvents.value.push({ time, msg })
    if (browserPreviewEvents.value.length > 30) {
      browserPreviewEvents.value = browserPreviewEvents.value.slice(-30)
    }
  }

  function onPreviewClick(e: MouseEvent) {
    if (!previewWs || previewWs.readyState !== WebSocket.OPEN) return
    const img = e.target as HTMLImageElement
    if (!img || !img.naturalWidth || !img.naturalHeight) return
    // 计算object-fit:contain下图片实际渲染位置（去抛letterbox黑边）
    const rect = img.getBoundingClientRect()
    const imgAspect = img.naturalWidth / img.naturalHeight
    const boxAspect = rect.width / rect.height
    let renderW: number, renderH: number, offsetX: number, offsetY: number
    if (imgAspect > boxAspect) {
      // 图片更宽，上下有黑边
      renderW = rect.width
      renderH = rect.width / imgAspect
      offsetX = 0
      offsetY = (rect.height - renderH) / 2
    } else {
      // 图片更高，左右有黑边
      renderH = rect.height
      renderW = rect.height * imgAspect
      offsetX = (rect.width - renderW) / 2
      offsetY = 0
    }
    // 点击坐标相对于实际渲染图片的位置
    const relX = e.clientX - rect.left - offsetX
    const relY = e.clientY - rect.top - offsetY
    if (relX < 0 || relY < 0 || relX > renderW || relY > renderH) return
    // 映射到浏览器viewport坐标（使用后端发送的viewport尺寸，否则用naturalWidth）
    const vpW = browserPreviewFrame.value?.viewportWidth || img.naturalWidth
    const vpH = browserPreviewFrame.value?.viewportHeight || img.naturalHeight
    const x = (relX / renderW) * vpW
    const y = (relY / renderH) * vpH
    previewWs.send(JSON.stringify({ type: 'click', x: Math.round(x), y: Math.round(y) }))
    addPreviewEvent(`点击 (${Math.round(x)}, ${Math.round(y)})`)
    // 点击后让图片获取焦点以接收键盘事件
    img.focus()
  }

  function onPreviewKeydown(e: KeyboardEvent) {
    if (!previewWs || previewWs.readyState !== WebSocket.OPEN) return
    // 不拦截带 Ctrl/Alt 的组合键（浏览器快捷键）
    if (e.ctrlKey || e.altKey || e.metaKey) return
    e.preventDefault()
    previewWs.send(JSON.stringify({ type: 'keypress', key: e.key, text: e.key.length === 1 ? e.key : '' }))
    addPreviewEvent(`按键 [${e.key}]`)
  }

  function onPreviewScroll(e: WheelEvent) {
    if (!previewWs || previewWs.readyState !== WebSocket.OPEN) return
    const img = e.target as HTMLImageElement
    if (!img || !img.naturalWidth || !img.naturalHeight) return
    const rect = img.getBoundingClientRect()
    const imgAspect = img.naturalWidth / img.naturalHeight
    const boxAspect = rect.width / rect.height
    let renderW: number, renderH: number, offsetX: number, offsetY: number
    if (imgAspect > boxAspect) {
      renderW = rect.width
      renderH = rect.width / imgAspect
      offsetX = 0
      offsetY = (rect.height - renderH) / 2
    } else {
      renderH = rect.height
      renderW = rect.height * imgAspect
      offsetX = (rect.width - renderW) / 2
      offsetY = 0
    }
    const relX = e.clientX - rect.left - offsetX
    const relY = e.clientY - rect.top - offsetY
    if (relX < 0 || relY < 0 || relX > renderW || relY > renderH) return
    const vpW = browserPreviewFrame.value?.viewportWidth || img.naturalWidth
    const vpH = browserPreviewFrame.value?.viewportHeight || img.naturalHeight
    const x = (relX / renderW) * vpW
    const y = (relY / renderH) * vpH
    previewWs.send(JSON.stringify({ type: 'scroll', x: Math.round(x), y: Math.round(y), deltaX: e.deltaX, deltaY: e.deltaY }))
  }

  function sendPreviewText() {
    if (!previewWs || previewWs.readyState !== WebSocket.OPEN || !previewInputText.value) return
    previewWs.send(JSON.stringify({ type: 'type_text', text: previewInputText.value }))
    addPreviewEvent(`输入: ${previewInputText.value}`)
    previewInputText.value = ''
  }

  function navigatePreview() {
    const url = previewAddressUrl.value.trim()
    if (!url) return
    // 通过WebSocket发送导航请求
    if (previewWs && previewWs.readyState === WebSocket.OPEN) {
      previewWs.send(JSON.stringify({ type: 'navigate', url }))
      addPreviewEvent(`导航到: ${url}`)
    }
  }

  async function manualSaveCookies() {
    savingCookies.value = true
    try {
      const { data } = await v2Api.xhsSaveCookies()
      if (data.ok) {
        const info = data.nickname ? `${data.nickname} (${data.red_id || data.user_id || '未知'})` : `${data.cookies_count}个Cookie`
        Message.success(`Cookie已保存: ${info}`)
        loadAccounts()
      } else {
        Message.warning(`保存失败: ${data.error || '未知错误'}`)
      }
    } catch (e: any) {
      Message.error(`保存失败: ${e.message || '请求错误'}`)
    } finally {
      savingCookies.value = false
    }
  }

  async function resetBrowserData() {
    resettingBrowser.value = true
    try {
      const { data } = await v2Api.xhsResetBrowser()
      if (data.ok) {
        Message.success(`浏览器已重置: ${data.message}`)
      } else {
        Message.warning(`重置失败: ${data.error || '未知错误'}`)
      }
    } catch (e: any) {
      Message.error(`重置失败: ${e.message || '请求错误'}`)
    } finally {
      resettingBrowser.value = false
    }
  }

  function refreshQrcode() {
    if (qrcodeLoginType.value === 'qq') {
      loginQQ()
    } else if (qrcodeLoginType.value === 'xhs') {
      loginXHS()
    }
  }

  async function loginXHS() {
    cleanupLoginPoll()
    xhsLogging.value = true
    qrcodeData.value = ''
    qrcodeLoginType.value = 'xhs'
    loginStatusText.value = '正在获取二维码...'
    loginStatusColor.value = 'var(--text-muted)'
    try {
      const { data } = await v2Api.getXHSQrcode()
      qrcodeData.value = data.qrcode || ''
      if (!qrcodeData.value) { Message.warning('获取二维码失败'); xhsLogging.value = false; return }
      // 自动连接浏览器实时预览
      startBrowserPreview('xhs')
      loginStatusText.value = '请使用手机扫描二维码'
      loginStatusColor.value = ''
      // 轮询状态
      loginPollInterval = setInterval(async () => {
        try {
          const { data: status } = await v2Api.getXHSStatus()
          if (status.detail && status.status !== 'logged_in') {
            loginStatusText.value = status.detail
            loginStatusColor.value = status.status === 'error' ? 'var(--color-danger-6)' : ''
          }
          if (status.status === 'logged_in') {
            cleanupLoginPoll()
            if (!keepPreviewAfterLogin.value) { stopBrowserPreview() }
            xhsLogging.value = false
            qrcodeData.value = ''
            loginStatusText.value = ''
            Message.success('小红书登录成功！')
            loadAccounts()
          } else if (status.status === 'expired') {
            loginStatusText.value = '⏰ 二维码已过期，正在刷新...'
            loginStatusColor.value = 'var(--color-warning-6)'
            cleanupLoginPoll()
            setTimeout(() => loginXHS(), 1000)
          } else if (status.status === 'error') {
            loginStatusText.value = status.detail || '❌ 登录出错'
            loginStatusColor.value = 'var(--color-danger-6)'
            cleanupLoginPoll()
            xhsLogging.value = false
            Message.error(status.detail || '小红书登录失败')
            // 如果是"登录异常"自动重置，提示用户重新获取二维码
            if (status.detail && status.detail.includes('自动重置')) {
              Message.warning('浏览器数据已自动重置，请重新点击登录获取二维码')
            }
          } else if (status.status === 'waiting_scan') {
            loginStatusText.value = status.detail || '等待扫码...'
            loginStatusColor.value = ''
          }
        } catch (e: any) {
          const message = getErrorMessage(e, '小红书登录状态检查失败')
          loginStatusText.value = message
          loginStatusColor.value = 'var(--color-danger-6)'
          cleanupLoginPoll()
          xhsLogging.value = false
          Message.error(message)
        }
      }, 3000)
      setTimeout(() => { cleanupLoginPoll(); xhsLogging.value = false }, 200000)
    } catch (e: any) {
      xhsLogging.value = false
      Message.error(e?.response?.data?.detail || '获取二维码失败')
    }
  }

  async function refreshQQCookies() {
    cookieRefreshing.value = true
    try {
      const { data } = await v2Api.refreshQQCookies()
      if (data.success) {
        Message.success('QQ Cookie续期成功')
      } else {
        Message.warning(data.message || 'Cookie续期失败，请重新扫码登录')
      }
    } catch (e: any) {
      Message.error(e?.response?.data?.detail || 'Cookie续期请求失败')
    } finally {
      cookieRefreshing.value = false
      loadAccounts()
    }
  }

  async function loginViaNapcat() {
    napcatLogging.value = true
    try {
      const { data } = await v2Api.qqNapcatLogin()
      Message.success(data.message || 'NapCat登录成功！')
      loadAccounts()
    } catch (e: any) {
      Message.error(e?.response?.data?.detail || 'NapCat登录失败')
    } finally {
      napcatLogging.value = false
    }
  }

  async function loginQQ() {
    cleanupLoginPoll()
    qqLogging.value = true
    qrcodeData.value = ''
    qrcodeLoginType.value = 'qq'
    loginStatusText.value = '正在获取二维码...'
    loginStatusColor.value = 'var(--text-muted)'
    try {
      const { data } = await v2Api.getQQQrcode()
      qrcodeData.value = data.qrcode || ''
      if (!qrcodeData.value) { Message.warning('获取QQ二维码失败'); qqLogging.value = false; return }
      // 自动连接浏览器实时预览
      startBrowserPreview('qq')
      loginStatusText.value = '请使用手机QQ扫描二维码'
      loginStatusColor.value = ''
      // 轮询状态
      loginPollInterval = setInterval(async () => {
        try {
          const { data: status } = await v2Api.getQQStatus()
          if (status.detail && status.status !== 'logged_in') {
            loginStatusText.value = status.detail
            loginStatusColor.value = status.status === 'error' ? 'var(--color-danger-6)' : ''
          }
          if (status.status === 'logged_in') {
            cleanupLoginPoll()
            if (!keepPreviewAfterLogin.value) { stopBrowserPreview() }
            qqLogging.value = false
            qrcodeData.value = ''
            loginStatusText.value = ''
            Message.success('QQ空间登录成功！')
            loadAccounts()
          } else if (status.status === 'expired') {
            loginStatusText.value = '⏰ 二维码已过期，正在刷新...'
            loginStatusColor.value = 'var(--color-warning-6)'
            cleanupLoginPoll()
            setTimeout(() => loginQQ(), 1000)
          } else if (status.status === 'error') {
            loginStatusText.value = '❌ 登录出错'
            loginStatusColor.value = 'var(--color-danger-6)'
            cleanupLoginPoll()
            qqLogging.value = false
          } else if (status.status === 'waiting_scan') {
            loginStatusText.value = status.detail || '等待扫码...'
            loginStatusColor.value = ''
          }
        } catch (e) { console.error('QQ状态轮询异常', e) }
      }, 3000)
      setTimeout(() => { cleanupLoginPoll(); qqLogging.value = false }, 200000)
    } catch (e: any) {
      qqLogging.value = false
      Message.error(e.response?.data?.detail || '获取QQ二维码失败')
    }
  }

  onMounted(() => {
    loadAccounts()
    loadAIConfigs()
    loadDbConfig()
    loadRedisConfig()
    loadSystemConfigs()
  })

  onUnmounted(() => {
    if (logRefreshTimer) { clearInterval(logRefreshTimer); logRefreshTimer = null }
    cleanupLoginPoll()
    stopBrowserPreview()
  })

  return {
    settingKeyMap,
    reverseSettingKeyMap,
    systemApi,
    accounts,
    monitoredAccounts,
    loginAccounts,
    monitoredQQAccounts,
    monitoredXHSAccounts,
    loginQQAccounts,
    loginXHSAccounts,
    activeAccountCount,
    elevatedLoginRiskCount,
    cooldownLoginCount,
    accountModalVisible,
    accountForm,
    refreshingId,
    refreshingAll,
    aiConfigs,
    configModalVisible,
    configSaving,
    editingConfig,
    configForm,
    aiTesting,
    aiTestResult,
    dbForm,
    dbTesting,
    dbTestResult,
    redisForm,
    redisTesting,
    redisSaving,
    redisTestResult,
    napcatUrl,
    napcatToken,
    logs,
    logsLoading,
    logLevel,
    logKeyword,
    logModule,
    logAutoRefresh,
    logTotalBuffered,
    logRefreshTimer,
    autoCrawlEnabled,
    autoCrawlInterval,
    skipCrawlWhenLoginDegraded,
    cookieFailureThreshold,
    riskCooldownMinutes,
    xhsCrawlDetailDelaySeconds,
    xhsProfileScrollDelaySeconds,
    aiContextMode,
    summaryStats,
    summaryLoading,
    summaryGenerating,
    autoSummaryEnabled,
    aiVisionEnabled,
    serverBaseUrl,
    loginNotifyEnabled,
    adminForm,
    adminSaving,
    qrcodeData,
    xhsLogging,
    qqLogging,
    cookieRefreshing,
    napcatLogging,
    qrcodeLoginType,
    loginStatusText,
    loginStatusColor,
    loginPollInterval,
    loginStatusToneClass,
    browserPreviewActive,
    browserPreviewConnected,
    browserPreviewFrame,
    browserPreviewEvents,
    previewFps,
    previewInputText,
    previewAddressUrl,
    addressBarFocused,
    savingCookies,
    resettingBrowser,
    previewWs,
    previewFrameCount,
    previewFpsTimer,
    keepPreviewAfterLogin,
    previewState,
    getErrorMessage,
    normalizeAccountStatus,
    getAccountStatusBadge,
    getAccountToggleType,
    getAccountToggleLabel,
    formatShortDateTime,
    shortenRiskReason,
    formatRiskSummary,
    loadAccounts,
    loadAIConfigs,
    loadDbConfig,
    loadRedisConfig,
    showAddAccount,
    addAccount,
    removeAccount,
    toggleAccount,
    refreshAccountInfo,
    refreshAllAccountsAction,
    getAvatarSrc,
    getAvatarCdnFallback,
    showAddConfig,
    editConfig,
    saveConfig,
    activateConfig,
    deleteConfig,
    testAIConfig,
    testDbConnection,
    saveDbConfig,
    testRedisConnection,
    saveRedisConfig,
    saveNapcatConfig,
    saveLoginNotifyConfig,
    saveAutoCrawlConfig,
    saveRiskControlConfig,
    saveXhsCrawlTimingConfig,
    saveAutoSummaryEnabled,
    saveVisionConfig,
    saveAIContextMode,
    loadSummaryStats,
    generateSummaries,
    loadSystemConfigs,
    saveAdminConfig,
    saveAdminSafety,
    loadLogs,
    clearLogs,
    copyLogs,
    toggleAutoRefresh,
    cleanupLoginPoll,
    startBrowserPreview,
    stopBrowserPreview,
    addPreviewEvent,
    onPreviewClick,
    onPreviewKeydown,
    onPreviewScroll,
    sendPreviewText,
    navigatePreview,
    manualSaveCookies,
    resetBrowserData,
    refreshQrcode,
    loginXHS,
    refreshQQCookies,
    loginViaNapcat,
    loginQQ,
  }
}
