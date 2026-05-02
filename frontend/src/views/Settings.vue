<template>
  <div class="page-container settings-page">
    <a-tabs default-active-key="accounts" size="large" class="settings-tabs-shell">
      <a-tab-pane key="accounts">
        <template #title><icon-user /> 账号管理</template>
        <div class="settings-card">
        <div class="settings-section">
          <div class="settings-overview-grid">
            <div class="settings-overview-card">
              <span class="settings-overview-label">监控账号</span>
              <strong class="settings-overview-value">{{ monitoredAccounts.length }}</strong>
            </div>
            <div class="settings-overview-card">
              <span class="settings-overview-label">登录账号</span>
              <strong class="settings-overview-value">{{ loginAccounts.length }}</strong>
            </div>
            <div class="settings-overview-card">
              <span class="settings-overview-label">当前启用</span>
              <strong class="settings-overview-value">{{ activeAccountCount }}</strong>
            </div>
            <div class="settings-overview-card">
              <span class="settings-overview-label">风险登录</span>
              <strong class="settings-overview-value">{{ elevatedLoginRiskCount }}</strong>
            </div>
            <div class="settings-overview-card">
              <span class="settings-overview-label">冷却账号</span>
              <strong class="settings-overview-value">{{ cooldownLoginCount }}</strong>
            </div>
            <div class="settings-overview-card accent">
              <span class="settings-overview-label">账号同步</span>
              <strong class="settings-overview-value">{{ refreshingAll ? '进行中' : '待命' }}</strong>
            </div>
          </div>

          <div class="section-header">
            <h3>监控账号</h3>
            <a-space>
              <a-button type="outline" @click="refreshAllAccountsAction" :loading="refreshingAll">
                <template #icon><icon-refresh /></template>
                一键刷新全部
              </a-button>
              <a-button type="primary" @click="showAddAccount">
                <template #icon><icon-plus /></template>
                添加账号
              </a-button>
            </a-space>
          </div>
          <p class="section-desc">要爬取动态的目标账号（被监控的人）</p>
          <div v-if="refreshingAll" class="inline-state-card compact">
            <div>
              <strong>正在批量刷新账号资料</strong>
              <p>系统会重新同步头像、昵称和签名，完成后自动刷新当前列表。</p>
            </div>
          </div>

          <!-- QQ 监控账号 -->
          <div v-if="monitoredQQAccounts.length > 0" class="settings-data-block">
            <div class="platform-label platform-qq">
              <a-tag color="blue" size="medium">QQ</a-tag> 监控账号
            </div>
            <a-table :data="monitoredQQAccounts" :pagination="false" size="medium" class="settings-table">
              <template #columns>
                <a-table-column title="头像" :width="60">
                  <template #cell="{ record }">
                    <a-avatar :size="32" :image-url="getAvatarSrc(record.avatar_url) || getAvatarCdnFallback(record)" shape="circle" />
                  </template>
                </a-table-column>
                <a-table-column title="昵称" :width="140">
                  <template #cell="{ record }">
                    <a-popover v-if="record.nickname_history && record.nickname_history.length" trigger="click" position="right">
                      <span class="clickable-nickname">{{ record.nickname || '-' }}</span>
                      <template #content>
                        <div class="popover-history">
                          <div class="popover-history-title">昵称历史 ({{ record.nickname_history.length }})</div>
                          <div v-for="(h, i) in record.nickname_history.slice(-10)" :key="i" class="popover-history-item">
                            <span class="history-name">{{ h.nickname }}</span>
                            <span class="history-time">{{ h.time?.slice(0,10) }}</span>
                          </div>
                        </div>
                      </template>
                    </a-popover>
                    <span v-else>{{ record.nickname || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="QQ号" data-index="account_id" :width="150" />
                <a-table-column title="签名" :ellipsis="true">
                  <template #cell="{ record }">
                    <span class="cell-secondary-text">{{ record.signature || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="状态" :width="90">
                  <template #cell="{ record }">
                    <a-badge :status="getAccountStatusBadge(record).badgeStatus" :text="getAccountStatusBadge(record).text" />
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="200">
                  <template #cell="{ record }">
                    <a-space>
                      <a-tooltip content="从平台获取最新昵称、头像和签名">
                        <a-button size="small" type="outline" @click="refreshAccountInfo(record.id)" :loading="refreshingId === record.id">
                          <template #icon><icon-refresh /></template>
                        </a-button>
                      </a-tooltip>
                      <a-button size="small" :type="getAccountToggleType(record)" @click="toggleAccount(record.id)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="确定删除？" @ok="removeAccount(record.id)">
                        <a-button size="small" status="danger"><icon-delete /></a-button>
                      </a-popconfirm>
                    </a-space>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>

          <!-- 小红书 监控账号 -->
          <div v-if="monitoredXHSAccounts.length > 0" class="settings-data-block">
            <div class="platform-label platform-xhs">
              <a-tag color="red" size="medium">小红书</a-tag> 监控账号
            </div>
            <a-table :data="monitoredXHSAccounts" :pagination="false" size="medium" class="settings-table">
              <template #columns>
                <a-table-column title="头像" :width="60">
                  <template #cell="{ record }">
                    <a-popover v-if="record.avatar_history && record.avatar_history.length" trigger="click" position="right">
                      <a-avatar :size="32" :image-url="getAvatarSrc(record.avatar_url)" shape="circle" class="clickable-avatar" />
                      <template #content>
                        <div class="popover-history">
                          <div class="popover-history-title">头像历史 ({{ record.avatar_history.length }})</div>
                          <div v-for="(h, i) in record.avatar_history.slice(-5)" :key="i" class="popover-avatar-item">
                            <a-avatar :size="28" :image-url="getAvatarSrc(h.url)" shape="circle" />
                            <span class="history-time">{{ h.time?.slice(0,10) }}</span>
                          </div>
                        </div>
                      </template>
                    </a-popover>
                    <a-avatar v-else :size="32" :image-url="getAvatarSrc(record.avatar_url)" shape="circle" />
                  </template>
                </a-table-column>
                <a-table-column title="昵称" :width="140">
                  <template #cell="{ record }">
                    <a-popover v-if="record.nickname_history && record.nickname_history.length" trigger="click" position="right">
                      <span class="clickable-nickname">{{ record.nickname || '-' }}</span>
                      <template #content>
                        <div class="popover-history">
                          <div class="popover-history-title">昵称历史 ({{ record.nickname_history.length }})</div>
                          <div v-for="(h, i) in record.nickname_history.slice(-10)" :key="i" class="popover-history-item">
                            <span class="history-name">{{ h.nickname }}</span>
                            <span class="history-time">{{ h.time?.slice(0,10) }}</span>
                          </div>
                        </div>
                      </template>
                    </a-popover>
                    <span v-else>{{ record.nickname || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="红薯号" data-index="account_id" :width="150" />
                <a-table-column title="ObjectId" :width="200">
                  <template #cell="{ record }">
                    <span v-if="record.platform_uid" class="cell-mono-text">{{ record.platform_uid }}</span>
                    <span v-else class="cell-muted-text">-</span>
                  </template>
                </a-table-column>
                <a-table-column title="个人简介" :ellipsis="true">
                  <template #cell="{ record }">
                    <span class="cell-secondary-text">{{ record.signature || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="状态" :width="90">
                  <template #cell="{ record }">
                    <a-badge :status="getAccountStatusBadge(record).badgeStatus" :text="getAccountStatusBadge(record).text" />
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="200">
                  <template #cell="{ record }">
                    <a-space>
                      <a-tooltip content="从平台获取最新昵称、头像和个人简介">
                        <a-button size="small" type="outline" @click="refreshAccountInfo(record.id)" :loading="refreshingId === record.id">
                          <template #icon><icon-refresh /></template>
                        </a-button>
                      </a-tooltip>
                      <a-button size="small" :type="getAccountToggleType(record)" @click="toggleAccount(record.id)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="确定删除？" @ok="removeAccount(record.id)">
                        <a-button size="small" status="danger"><icon-delete /></a-button>
                      </a-popconfirm>
                    </a-space>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>

          <div v-if="monitoredAccounts.length === 0" class="sub-text settings-empty-card">
            暂无监控账号，点击上方"添加账号"来添加要爬取动态的目标
          </div>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>扫码登录</h3>
          <p class="section-desc settings-desc-compact">登录你自己的账号（查看者），系统通过Cookie爬取上面监控账号的动态。</p>
          <!-- QQ 登录账号 -->
          <div v-if="loginQQAccounts.length > 0" class="settings-data-block">
            <div class="platform-label platform-qq">
              <a-tag color="blue" size="small">QQ</a-tag> 登录账号
            </div>
            <a-table :data="loginQQAccounts" :pagination="false" size="small" class="settings-table">
              <template #columns>
                <a-table-column title="头像" :width="50">
                  <template #cell="{ record }">
                    <a-avatar :size="28" :image-url="getAvatarSrc(record.avatar_url) || getAvatarCdnFallback(record)" shape="circle" />
                  </template>
                </a-table-column>
                <a-table-column title="昵称" :width="100">
                  <template #cell="{ record }">
                    <span>{{ record.nickname || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="QQ号" :width="150">
                  <template #cell="{ record }">
                    <span class="cell-small-text">{{ record.account_id }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="状态" :width="80">
                  <template #cell="{ record }">
                    <a-badge :status="getAccountStatusBadge(record, 'login').badgeStatus" :text="getAccountStatusBadge(record, 'login').text" />
                  </template>
                </a-table-column>
                <a-table-column title="风控" :width="220">
                  <template #cell="{ record }">
                    <div>
                      <div class="cell-secondary-text">{{ formatRiskSummary(record) }}</div>
                      <div v-if="record.last_cookie_refresh_at" class="cell-muted-text">最近续期 {{ formatShortDateTime(record.last_cookie_refresh_at) }}</div>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="登录时间" :width="160">
                  <template #cell="{ record }">
                    {{ record.last_login ? record.last_login.replace('T', ' ').slice(0, 19) : '-' }}
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="150">
                  <template #cell="{ record }">
                    <a-space size="mini">
                      <a-tooltip content="刷新账号信息">
                        <a-button size="mini" type="outline" @click="refreshAccountInfo(record.id)" :loading="refreshingId === record.id">
                          <template #icon><icon-refresh /></template>
                        </a-button>
                      </a-tooltip>
                      <a-button size="mini" :type="getAccountToggleType(record)" @click="toggleAccount(record.id)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="删除此登录账号？" @ok="removeAccount(record.id)">
                        <a-button size="mini" status="danger"><icon-delete /></a-button>
                      </a-popconfirm>
                    </a-space>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
          <!-- 小红书 登录账号 -->
          <div v-if="loginXHSAccounts.length > 0" class="settings-data-block">
            <div class="platform-label platform-xhs">
              <a-tag color="red" size="small">小红书</a-tag> 登录账号
            </div>
            <a-table :data="loginXHSAccounts" :pagination="false" size="small" class="settings-table">
              <template #columns>
                <a-table-column title="头像" :width="50">
                  <template #cell="{ record }">
                    <a-popover v-if="record.avatar_history && record.avatar_history.length" trigger="click" position="right">
                      <a-avatar :size="28" :image-url="getAvatarSrc(record.avatar_url)" shape="circle" class="clickable-avatar" />
                      <template #content>
                        <div class="popover-history">
                          <div class="popover-history-title">头像历史 ({{ record.avatar_history.length }})</div>
                          <div v-for="(h, i) in record.avatar_history.slice(-5)" :key="i" class="popover-avatar-item">
                            <a-avatar :size="28" :image-url="getAvatarSrc(h.url)" shape="circle" />
                            <span class="history-time">{{ h.time?.slice(0,10) }}</span>
                          </div>
                        </div>
                      </template>
                    </a-popover>
                    <a-avatar v-else :size="28" :image-url="getAvatarSrc(record.avatar_url)" shape="circle" />
                  </template>
                </a-table-column>
                <a-table-column title="昵称" :width="100">
                  <template #cell="{ record }">
                    <a-popover v-if="record.nickname_history && record.nickname_history.length" trigger="click" position="right">
                      <span class="clickable-nickname">{{ record.nickname || '-' }}</span>
                      <template #content>
                        <div class="popover-history">
                          <div class="popover-history-title">昵称历史 ({{ record.nickname_history.length }})</div>
                          <div v-for="(h, i) in record.nickname_history.slice(-10)" :key="i" class="popover-history-item">
                            <span class="history-name">{{ h.nickname }}</span>
                            <span class="history-time">{{ h.time?.slice(0,10) }}</span>
                          </div>
                        </div>
                      </template>
                    </a-popover>
                    <span v-else>{{ record.nickname || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="红薯号" :width="140">
                  <template #cell="{ record }">
                    <span class="cell-small-text">{{ record.account_id }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="ObjectId" :width="200">
                  <template #cell="{ record }">
                    <span v-if="record.platform_uid" class="cell-mono-text">{{ record.platform_uid }}</span>
                    <span v-else class="cell-muted-text">-</span>
                  </template>
                </a-table-column>
                <a-table-column title="状态" :width="80">
                  <template #cell="{ record }">
                    <a-badge :status="getAccountStatusBadge(record, 'login').badgeStatus" :text="getAccountStatusBadge(record, 'login').text" />
                  </template>
                </a-table-column>
                <a-table-column title="风控" :width="220">
                  <template #cell="{ record }">
                    <div>
                      <div class="cell-secondary-text">{{ formatRiskSummary(record) }}</div>
                      <div v-if="record.last_cookie_refresh_at" class="cell-muted-text">最近续期 {{ formatShortDateTime(record.last_cookie_refresh_at) }}</div>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="登录时间" :width="160">
                  <template #cell="{ record }">
                    {{ record.last_login ? record.last_login.replace('T', ' ').slice(0, 19) : '-' }}
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="150">
                  <template #cell="{ record }">
                    <a-space size="mini">
                      <a-tooltip content="刷新账号信息">
                        <a-button size="mini" type="outline" @click="refreshAccountInfo(record.id)" :loading="refreshingId === record.id">
                          <template #icon><icon-refresh /></template>
                        </a-button>
                      </a-tooltip>
                      <a-button size="mini" :type="getAccountToggleType(record)" @click="toggleAccount(record.id)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="删除此登录账号？" @ok="removeAccount(record.id)">
                        <a-button size="mini" status="danger"><icon-delete /></a-button>
                      </a-popconfirm>
                    </a-space>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
          <div class="login-grid">
            <a-button @click="loginQQ" :loading="qqLogging" size="large" type="primary">
              <template #icon><icon-scan /></template>
              QQ空间扫码登录
            </a-button>
            <a-button @click="loginXHS" :loading="xhsLogging" size="large" type="primary">
              <template #icon><icon-scan /></template>
              小红书扫码登录
            </a-button>
            <a-button @click="loginViaNapcat" :loading="napcatLogging" size="large" type="outline" status="success">
              <template #icon><icon-robot /></template>
              NapCat自动登录
            </a-button>
            <a-button @click="refreshQQCookies" :loading="cookieRefreshing" size="large" type="outline">
              <template #icon><icon-refresh /></template>
              QQ Cookie续期
            </a-button>
          </div>
          <div v-if="qrcodeData" class="qrcode-area">
            <img :src="'data:image/png;base64,' + qrcodeData" class="qrcode-img" />
            <p v-if="loginStatusText" :class="['qrcode-status', loginStatusToneClass]">{{ loginStatusText }}</p>
            <p v-else>{{ qrcodeLoginType === 'qq' ? '请使用手机QQ扫描二维码' : '请使用手机扫描二维码' }}</p>
            <a-button size="small" type="text" @click="refreshQrcode" class="qrcode-refresh-btn">
              <template #icon><icon-refresh /></template>
              刷新二维码
            </a-button>
          </div>

          <!-- 实时浏览器预览面板 -->
          <div v-if="browserPreviewActive || qrcodeData" class="browser-preview-panel">
            <div class="preview-header">
              <div class="preview-title">
                <span class="preview-dot" :class="browserPreviewConnected ? 'connected' : 'disconnected'"></span>
                后端浏览器实时预览
                <a-tag v-if="browserPreviewConnected" color="green" size="small">已连接</a-tag>
                <a-tag v-else color="gray" size="small">未连接</a-tag>
              </div>
              <a-space size="small">
                <a-tooltip content="开启后登录成功不会立即关闭浏览器，可继续在预览中浏览和操作">
                  <span class="preview-switch-control">
                    <a-switch v-model="keepPreviewAfterLogin" size="small" />
                    <span class="preview-switch-label">
                      {{ keepPreviewAfterLogin ? '登录后保持预览' : '登录后关闭' }}
                    </span>
                  </span>
                </a-tooltip>
                <a-button v-if="!browserPreviewConnected && qrcodeLoginType" size="mini" type="primary" @click="startBrowserPreview(qrcodeLoginType)">
                  连接预览
                </a-button>
                <a-button v-if="browserPreviewConnected" size="mini" status="danger" @click="stopBrowserPreview">
                  断开
                </a-button>
              </a-space>
            </div>
            <div class="preview-info" v-if="browserPreviewFrame">
              <div class="info-row">
                <span class="info-label">状态:</span>
                <span :class="'status-' + (browserPreviewFrame.status || 'unknown')">{{ browserPreviewFrame.detail || browserPreviewFrame.status || '-' }}</span>
              </div>
              <div class="info-row address-bar">
                <span class="info-label">URL:</span>
                <a-input
                  v-model="previewAddressUrl"
                  size="mini"
                  placeholder="输入网址后按回车导航"
                  @keydown.enter="navigatePreview"
                  @focus="addressBarFocused = true"
                  @blur="addressBarFocused = false"
                  class="preview-address-input"
                />
                <a-button size="mini" type="primary" @click="navigatePreview" :disabled="!previewAddressUrl" class="preview-action-button">
                  <template #icon><icon-arrow-right /></template>
                </a-button>
                <a-tooltip content="手动保存当前浏览器Cookie到数据库">
                  <a-button size="mini" type="outline" status="success" @click="manualSaveCookies" :loading="savingCookies" class="preview-action-button">
                    <template #icon><icon-save /></template>
                    保存Cookie
                  </a-button>
                </a-tooltip>
                <a-popconfirm content="将清除浏览器指纹和Cookie缓存，需要重新登录" @ok="resetBrowserData">
                  <a-button size="mini" type="outline" status="danger" :loading="resettingBrowser" class="preview-action-button">
                    <template #icon><icon-delete /></template>
                    重置浏览器
                  </a-button>
                </a-popconfirm>
              </div>
            </div>
            <div class="preview-viewport" v-if="previewState === 'ready'">
              <img
                :src="'data:image/jpeg;base64,' + browserPreviewFrame.screenshot"
                class="preview-screenshot"
                @click="onPreviewClick"
                @wheel.prevent="onPreviewScroll"
                tabindex="0"
                @keydown="onPreviewKeydown"
                title="点击可将操作转发到后端浏览器，聚焦后可使用键盘输入"
              />
              <div class="preview-fps">{{ previewFps }} FPS</div>
            </div>
            <div v-else-if="previewState === 'booting'" class="preview-placeholder">
              <icon-loading /> 浏览器已连接，正在获取第一帧截图...
            </div>
            <div v-else-if="previewState === 'starting'" class="preview-placeholder">
              <icon-loading /> 浏览器连接已建立，正在等待登录页启动...
            </div>
            <div v-else-if="previewState === 'connecting'" class="preview-placeholder">
              <icon-loading /> 正在建立浏览器预览连接...
            </div>
            <div v-else-if="previewState === 'idle'" class="preview-placeholder">
              点击“连接预览”查看后端浏览器实时画面
            </div>
            <div v-if="browserPreviewConnected && previewState === 'ready'" class="preview-input-bar">
              <a-input
                v-model="previewInputText"
                placeholder="输入文字（如验证码），按回车发送"
                size="small"
                @keydown.enter="sendPreviewText"
                class="preview-text-input"
              >
                <template #prepend>
                  <icon-edit />
                </template>
              </a-input>
              <a-button size="small" type="primary" @click="sendPreviewText" :disabled="!previewInputText">
                发送
              </a-button>
            </div>
            <!-- 事件日志 -->
            <div v-if="browserPreviewEvents.length > 0" class="preview-events">
              <div v-for="(e, i) in browserPreviewEvents.slice(-8)" :key="i" class="event-line">
                <span class="event-time">{{ e.time }}</span>
                <span class="event-msg">{{ e.msg }}</span>
              </div>
            </div>
          </div>
        </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="ai">
        <template #title><icon-thunderbolt /> AI配置</template>
        <div class="settings-card">
        <div class="settings-section">
          <div class="section-header">
            <h3>AI服务配置</h3>
            <a-button type="primary" @click="showAddConfig">
              <template #icon><icon-plus /></template>
              添加配置
            </a-button>
          </div>
          <div v-for="config in aiConfigs" :key="config.id" class="config-card">
            <div class="config-header">
              <span class="config-name">{{ config.name }}</span>
              <a-tag v-if="config.is_active" color="green" size="medium">当前使用</a-tag>
              <div class="config-actions">
                <a-button v-if="!config.is_active" size="small" type="primary" @click="activateConfig(config.id)">激活</a-button>
                <a-button size="small" @click="editConfig(config)"><icon-edit /></a-button>
                <a-popconfirm content="确定删除？" @ok="deleteConfig(config.id)">
                  <a-button size="small" status="danger"><icon-delete /></a-button>
                </a-popconfirm>
              </div>
            </div>
            <div class="config-detail">
              <div class="config-detail-item">
                <span class="config-detail-label">API</span>
                <span>{{ config.api_base }}</span>
              </div>
              <div class="config-detail-item">
                <span class="config-detail-label">模型</span>
                <span>{{ config.model }}</span>
              </div>
              <div class="config-detail-item config-detail-secret">
                <span class="config-detail-label">Key</span>
                <span class="config-secret">{{ config.api_key }}</span>
              </div>
            </div>
          </div>
          <div v-if="aiConfigs.length === 0" class="sub-text settings-empty-card settings-empty-card-tight">
            暂无AI配置，请添加一个OpenAI兼容的API配置（支持newapi等第三方平台）
          </div>
        </div>
        </div>

        <!-- AI上下文模式配置 -->
        <div class="settings-card">
        <div class="settings-section">
          <h3>AI对话上下文模式</h3>
          <p class="section-desc">控制AI对话时如何引入动态记录。默认全量模式，当数据量过大超过上下文限制时可切换到智能模式。</p>

          <a-form :model="{ aiContextMode, aiVisionEnabled, serverBaseUrl }" layout="vertical" class="settings-form">
            <a-form-item label="上下文模式">
              <a-radio-group v-model="aiContextMode" direction="vertical" @change="saveAIContextMode">
                <a-radio value="full">
                  <div>
                    <strong>全量模式</strong>（推荐）
                    <div class="form-help-text">将所有动态完整内容（含图片URL）一并加入AI上下文。数据量超出模型上下文限制时自动降级为智能模式。</div>
                  </div>
                </a-radio>
                <a-radio value="auto">
                  <div>
                    <strong>自动模式</strong>
                    <div class="form-help-text">优先全量，超限时自动选择：如果有足够的总结数据则使用总结模式，否则使用搜索模式。</div>
                  </div>
                </a-radio>
                <a-radio value="smart_summary">
                  <div>
                    <strong>总结模式</strong>
                    <div class="form-help-text">使用AI预先生成的动态总结作为上下文。适合数据量非常大的场景，需先生成总结。</div>
                  </div>
                </a-radio>
                <a-radio value="smart_search">
                  <div>
                    <strong>搜索模式</strong>
                    <div class="form-help-text">AI自动解析提问内容，从数据库中搜索匹配的动态作为上下文。节省token但可能遗漏模糊问题。</div>
                  </div>
                </a-radio>
              </a-radio-group>
            </a-form-item>

            <!-- 图像理解 -->
            <a-form-item>
              <div class="switch-setting-row">
                <a-switch v-model="aiVisionEnabled" @change="saveVisionConfig" />
                <div>
                  <strong>图像理解</strong>
                  <div class="form-help-text">开启后AI可直接看到动态中的图片内容（需模型支持Vision API），会额外消耗图片token。</div>
                </div>
              </div>
            </a-form-item>
            <a-form-item v-if="aiVisionEnabled" label="服务器外部地址" help="AI API需通过此地址访问本地图片，如 http://你的IP:18100">
              <a-input v-model="serverBaseUrl" placeholder="http://your-server-ip:18100" @blur="saveVisionConfig" />
            </a-form-item>
          </a-form>

          <!-- 动态总结管理 -->
          <div class="summary-section">
            <div class="summary-header">
              <h4 class="summary-title">动态总结管理</h4>
              <a-space>
                <a-button size="small" type="outline" @click="loadSummaryStats" :loading="summaryLoading">
                  <template #icon><icon-refresh /></template>
                  刷新
                </a-button>
                <a-button size="small" type="primary" @click="generateSummaries(false)" :loading="summaryGenerating">
                  生成未总结
                </a-button>
                <a-popconfirm content="重新总结所有记录？这将消耗较多AI Token。" @ok="generateSummaries(true)">
                  <a-button size="small" status="warning" :loading="summaryGenerating">
                    全量重新总结
                  </a-button>
                </a-popconfirm>
              </a-space>
            </div>
            <div v-if="summaryStats" class="summary-stats-grid">
              <div class="stat-card-mini">
                <div class="stat-value">{{ summaryStats.total }}</div>
                <div class="stat-label">总动态数</div>
              </div>
              <div class="stat-card-mini">
                <div class="stat-value">{{ summaryStats.summarized }}</div>
                <div class="stat-label">已总结</div>
              </div>
              <div class="stat-card-mini">
                <div class="stat-value">{{ summaryStats.coverage }}%</div>
                <div class="stat-label">覆盖率</div>
              </div>
              <div class="stat-card-mini">
                <div class="stat-value">{{ summaryStats.last_summary_time ? summaryStats.last_summary_time.replace('T',' ').slice(0,16) : '-' }}</div>
                <div class="stat-label">最近总结时间</div>
              </div>
            </div>
            <div v-else class="summary-empty-state">
              点击"刷新"查看总结状态
            </div>
            <div class="summary-auto-row">
              <div>
                <span class="summary-auto-title">每周自动更新总结</span>
                <div class="form-help-text">开启后每周一凌晨3:00自动重新总结所有动态（包含纠正不准确的旧总结）</div>
              </div>
              <a-switch v-model="autoSummaryEnabled" @change="saveAutoSummaryEnabled" />
            </div>
          </div>
        </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="admin">
        <template #title><icon-safe /> 管理员</template>
        <div class="settings-card">
        <div class="settings-section">
          <h3>管理员配置</h3>
          <p class="section-desc">管理员QQ号用于接收所有AI监控任务的通知消息。未配置时任务无法发送通知。</p>
          <a-form :model="adminForm" layout="vertical" class="settings-form">
            <a-form-item label="管理员QQ号" help="监控任务触发时，通知将发送到此QQ号">
              <a-input v-model="adminForm.admin_qq" placeholder="输入你的QQ号" size="large" />
            </a-form-item>
            <a-form-item label="管理员昵称（可选）">
              <a-input v-model="adminForm.admin_name" placeholder="便于识别" size="large" />
            </a-form-item>
            <a-button type="primary" size="large" @click="saveAdminConfig" :loading="adminSaving">保存管理员配置</a-button>
          </a-form>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>通知安全</h3>
          <a-alert type="warning" class="settings-alert-warning">
            <div class="settings-alert-copy">
              <p>
                <strong>重要安全规则：</strong>
                系统默认 <strong>禁止</strong> 向被监控的QQ账号（即"监控账号"列表中的QQ号）发送任何消息。
              </p>
              <p>
                这是为了保护隐私。如果误将通知发送到被监控的人，会暴露你在监控他们的动态。
              </p>
            </div>
          </a-alert>
          <a-form :model="adminForm" layout="vertical" class="settings-form">
            <a-form-item label="允许向被监控账号发消息">
              <a-switch v-model="adminForm.allow_send_to_monitored" @change="saveAdminSafety">
                <template #checked>允许（危险）</template>
                <template #unchecked>禁止（推荐）</template>
              </a-switch>
              <div class="form-help-text form-help-danger" v-if="adminForm.allow_send_to_monitored">
                ⚠ 已允许向被监控账号发消息，这可能导致隐私泄露。请确认你知道自己在做什么。
              </div>
            </a-form-item>
          </a-form>
        </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="database">
        <template #title><icon-storage /> 数据库</template>
        <div class="settings-card">
        <div class="settings-section">
          <h3>数据库连接配置</h3>
          <a-form :model="dbForm" layout="vertical" class="settings-form">
            <a-form-item label="主机地址">
              <a-input v-model="dbForm.host" size="large" />
            </a-form-item>
            <a-form-item label="端口">
              <a-input-number v-model="dbForm.port" :min="1" :max="65535" size="large" />
            </a-form-item>
            <a-form-item label="数据库名">
              <a-input v-model="dbForm.name" size="large" />
            </a-form-item>
            <a-form-item label="用户名">
              <a-input v-model="dbForm.user" size="large" />
            </a-form-item>
            <a-form-item label="密码">
              <a-input-password v-model="dbForm.password" size="large" />
            </a-form-item>
            <a-space size="medium">
              <a-button type="primary" size="large" @click="testDbConnection" :loading="dbTesting">测试连接</a-button>
              <a-button size="large" @click="saveDbConfig">保存配置</a-button>
            </a-space>
          </a-form>
          <div v-if="dbTestResult" class="db-test-result" :class="dbTestResult.success ? 'success' : 'error'">
            {{ dbTestResult.message }}
          </div>
        </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="system">
        <template #title><icon-settings /> 系统</template>
        <div class="settings-card">
        <div class="settings-section">
          <h3>自动爬取</h3>
          <a-form :model="{ autoCrawlEnabled, autoCrawlInterval }" layout="vertical" class="settings-form">
            <a-form-item label="启用自动爬取">
              <a-switch v-model="autoCrawlEnabled" @change="saveAutoCrawlConfig" />
            </a-form-item>
            <a-form-item label="爬取间隔（分钟）">
              <a-input-number v-model="autoCrawlInterval" :min="5" :max="1440" :step="5" size="large" />
            </a-form-item>
            <a-button size="large" @click="saveAutoCrawlConfig">保存</a-button>
          </a-form>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>小红书抓取节奏</h3>
          <p class="section-desc">控制小红书列表滚动和详情页抓取之间的等待时间。数值越大越慢，但更接近人工浏览节奏。</p>
          <a-form :model="{ xhsCrawlDetailDelaySeconds, xhsProfileScrollDelaySeconds }" layout="vertical" class="settings-form">
            <a-form-item label="详情抓取间隔（秒）" help="每篇笔记详情、评论、媒体抓取后的等待时间；建议 5 秒以上。">
              <a-input-number v-model="xhsCrawlDetailDelaySeconds" :min="3" :max="60" :step="1" size="large" />
            </a-form-item>
            <a-form-item label="主页滚动间隔（秒）" help="用户主页滚动加载下一批笔记后的等待时间；建议 3 秒以上。">
              <a-input-number v-model="xhsProfileScrollDelaySeconds" :min="2" :max="30" :step="1" size="large" />
            </a-form-item>
            <a-button size="large" @click="saveXhsCrawlTimingConfig">保存小红书抓取节奏</a-button>
          </a-form>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>登录风控</h3>
          <p class="section-desc">控制登录账号进入降级、冷却或待重新登录后的自动抓取策略。</p>
          <a-form :model="{ skipCrawlWhenLoginDegraded, cookieFailureThreshold, riskCooldownMinutes }" layout="vertical" class="settings-form">
            <a-form-item>
              <div class="switch-setting-row">
                <a-switch v-model="skipCrawlWhenLoginDegraded" />
                <div>
                  <strong>登录账号降级时跳过自动抓取</strong>
                  <div class="form-help-text">开启后，只要登录账号进入 degraded、冷却或待重新登录状态，本轮自动抓取会直接 fail-closed。</div>
                </div>
              </div>
            </a-form-item>
            <a-form-item label="失败阈值" help="同一登录账号累计失败达到阈值后，会进入待重新登录状态。">
              <a-input-number v-model="cookieFailureThreshold" :min="1" :max="10" size="large" />
            </a-form-item>
            <a-form-item label="冷却时间（分钟）" help="失败后暂停再次使用该登录账号的时间窗口；0 表示不设置冷却。">
              <a-input-number v-model="riskCooldownMinutes" :min="0" :max="1440" size="large" />
            </a-form-item>
            <a-button size="large" @click="saveRiskControlConfig">保存风控配置</a-button>
          </a-form>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>NapCat连接</h3>
          <a-form :model="{ napcatUrl, napcatToken }" layout="vertical" class="settings-form">
            <a-form-item label="NapCat WebSocket地址">
              <a-input v-model="napcatUrl" placeholder="ws://127.0.0.1:3001" size="large" />
            </a-form-item>
            <a-form-item label="Token（可选）">
              <a-input-password v-model="napcatToken" size="large" />
            </a-form-item>
            <a-space size="medium">
              <a-button type="primary" size="large" @click="saveNapcatConfig">保存</a-button>
            </a-space>
          </a-form>
        </div>
        </div>

        <div class="settings-card">
        <div class="settings-section">
          <h3>通知设置</h3>
          <p class="section-desc">Cookie过期时通过QQ向管理员发送提醒，防止爬取任务失败</p>
          <a-form :model="{ loginNotifyEnabled }" layout="vertical" class="settings-form">
            <a-form-item label="Cookie过期通知">
              <a-switch v-model="loginNotifyEnabled" @change="saveLoginNotifyConfig">
                <template #checked>开启</template>
                <template #unchecked>关闭</template>
              </a-switch>
            </a-form-item>
          </a-form>
        </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="logs">
        <template #title><icon-file /> 日志</template>
        <div class="settings-card">
        <div class="settings-section">
          <div class="section-header settings-log-header">
            <h3>后端日志</h3>
            <div class="header-actions settings-log-toolbar">
              <a-select v-model="logLevel" placeholder="日志等级" class="settings-log-level" allow-clear @change="loadLogs" size="large">
                <a-option value="debug">DEBUG</a-option>
                <a-option value="info">INFO</a-option>
                <a-option value="warning">WARNING</a-option>
                <a-option value="error">ERROR</a-option>
              </a-select>
              <a-input v-model="logKeyword" placeholder="搜索关键词..." class="settings-log-keyword" allow-clear @press-enter="loadLogs" @clear="loadLogs" size="large" />
              <a-select v-model="logModule" placeholder="模块过滤" class="settings-log-module" allow-clear @change="loadLogs" size="large">
                <a-option value="ws">WebSocket</a-option>
                <a-option value="napcat">NapCat</a-option>
                <a-option value="qq_crawler">QQ爬虫</a-option>
                <a-option value="xhs_crawler">小红书爬虫</a-option>
                <a-option value="ai_service">AI服务</a-option>
                <a-option value="task_scheduler">定时任务</a-option>
                <a-option value="uvicorn">Uvicorn</a-option>
              </a-select>
              <a-button @click="loadLogs" :loading="logsLoading" size="large">
                <template #icon><icon-refresh /></template>
                刷新
              </a-button>
              <a-switch v-model="logAutoRefresh" @change="toggleAutoRefresh">
                <template #checked>自动</template>
                <template #unchecked>手动</template>
              </a-switch>
              <a-popconfirm content="确定清空所有日志？" @ok="clearLogs">
                <a-button status="danger">清空</a-button>
              </a-popconfirm>
              <a-button @click="copyLogs" type="outline">
                <template #icon><icon-copy /></template>
                复制日志
              </a-button>
            </div>
          </div>
          <div class="log-stats settings-log-stats">
            显示 {{ logs.length }} 条 / 缓冲 {{ logTotalBuffered }} 条
          </div>
          <div class="log-container settings-log-panel">
            <div v-if="logs.length === 0 && !logsLoading" class="empty-state settings-log-empty">
              <p>暂无日志</p>
              <p class="sub-text">后端启动后日志会自动记录</p>
            </div>
            <div v-for="(log, i) in logs" :key="i" class="log-line" :class="'log-' + log.level">
              <span class="log-time-col">{{ log.time?.replace('T', ' ').slice(11, 19) }}</span>
              <span class="log-level-col" :class="'level-' + log.level">{{ log.level?.toUpperCase() }}</span>
              <span class="log-module-col">{{ log.logger?.split('.').pop() || '' }}</span>
              <span class="log-msg-col">{{ log.message }}</span>
            </div>
          </div>
        </div>
        </div>
      </a-tab-pane>
    </a-tabs>

    <!-- 添加账号弹窗 -->
    <a-modal v-model:visible="accountModalVisible" title="添加监控账号" @ok="addAccount">
      <a-form :model="accountForm" layout="vertical">
        <a-form-item label="平台" required>
          <a-select v-model="accountForm.platform">
            <a-option value="qq">QQ</a-option>
            <a-option value="xhs">小红书</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="账号ID" required>
          <a-input v-model="accountForm.account_id" :placeholder="accountForm.platform === 'qq' ? 'QQ号' : '红薯号（如6012870660）'" />
          <template v-if="accountForm.platform === 'xhs'" #extra>
            <span class="form-help-inline">输入红薯号即可，系统会自动转换为内部UID</span>
          </template>
        </a-form-item>
        <a-form-item label="备注昵称">
          <a-input v-model="accountForm.nickname" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- AI配置弹窗 -->
    <a-modal v-model:visible="configModalVisible" :title="editingConfig ? '编辑AI配置' : '添加AI配置'" @ok="saveConfig" :ok-loading="configSaving">
      <a-form :model="configForm" layout="vertical">
        <a-form-item label="配置名称" required>
          <a-input v-model="configForm.name" placeholder="如：默认配置" />
        </a-form-item>
        <a-form-item label="API地址" required>
          <a-input v-model="configForm.api_base" placeholder="如：https://api.openai.com/v1" />
        </a-form-item>
        <a-form-item label="API Key" :required="!editingConfig">
          <a-input-password v-model="configForm.api_key" :placeholder="editingConfig ? '留空则保留当前密钥' : 'sk-...'" />
          <template v-if="editingConfig" #extra>
            <span class="form-help-inline">不再回显现有密钥；只有重新输入时才会更新。</span>
          </template>
        </a-form-item>
        <a-form-item label="模型名称" required>
          <a-input v-model="configForm.model" placeholder="如：gpt-4o-mini" />
        </a-form-item>
        <a-form-item label="Embedding模型">
          <a-input v-model="configForm.embed_model" placeholder="如：text-embedding-3-small（可选）" />
        </a-form-item>
        <a-form-item label="最大Token数">
          <a-input-number v-model="configForm.max_tokens" :min="256" :max="128000" />
        </a-form-item>
        <a-form-item label="Temperature">
          <a-slider v-model="configForm.temperature" :min="0" :max="10" :step="1" show-ticks />
        </a-form-item>
        <a-button size="small" @click="testAIConfig" :loading="aiTesting">测试连接</a-button>
        <span v-if="aiTestResult" :class="['test-result-inline', aiTestResult.success ? 'text-success' : 'text-error']">
          {{ aiTestResult.message }}
        </span>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { authApi, aiConfigApi, systemApi } from '@/api'
import { openAdminWebSocket } from '@/utils/adminToken'
import { Message } from '@arco-design/web-vue'
import { IconPlus, IconEdit, IconDelete, IconScan, IconRefresh, IconCopy, IconLoading, IconEye, IconRobot, IconArrowRight, IconSave, IconUser, IconThunderbolt, IconSafe, IconStorage, IconSettings, IconFile } from '@arco-design/web-vue/es/icon'

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
  embed_model: '', max_tokens: 4096, temperature: 7,
})
const aiTesting = ref(false)
const aiTestResult = ref<any>(null)

// 数据库
const dbForm = reactive({ host: '', port: 54320, name: '', user: '', password: '' })
const dbTesting = ref(false)
const dbTestResult = ref<any>(null)

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
    const detail = (error as any).response?.data?.detail
    if (typeof detail === 'string' && detail) return detail
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
    const { data } = await authApi.getAccounts()
    accounts.value = data || []
  } catch (e: any) {
    Message.error(getErrorMessage(e, '账号列表加载失败'))
  }
}

async function loadAIConfigs() {
  try {
    const { data } = await aiConfigApi.getConfigs()
    aiConfigs.value = data || []
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

function showAddAccount() {
  Object.assign(accountForm, { platform: 'qq', account_id: '', nickname: '' })
  accountModalVisible.value = true
}

async function addAccount() {
  if (!accountForm.account_id) { Message.warning('请输入账号ID'); return }
  try {
    await authApi.addAccount({ ...accountForm, is_target: 1 })
    Message.success('添加成功')
    accountModalVisible.value = false
    loadAccounts()
  } catch (e: any) { Message.error(e.response?.data?.detail || '添加失败') }
}

async function removeAccount(id: number) {
  try {
    await authApi.deleteAccount(id)
    Message.success('账号已删除')
    loadAccounts()
  } catch (e: any) {
    Message.error(getErrorMessage(e, '删除账号失败'))
  }
}

async function toggleAccount(id: number) {
  try {
    const { data } = await authApi.toggleAccount(id)
    Message.success(data.message || '操作成功')
    loadAccounts()
  } catch (e: any) { Message.error(e.response?.data?.detail || '操作失败') }
}

async function refreshAccountInfo(id: number) {
  refreshingId.value = id
  try {
    const { data } = await authApi.refreshAccount(id)
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
    const { data } = await authApi.refreshAllAccounts()
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
  if (url.startsWith('http')) return `/api/proxy/image?url=${encodeURIComponent(url)}`
  return url
}

function getAvatarCdnFallback(record: any): string {
  // QQ头像CDN回退（仅QQ平台有效）
  if (typeof record === 'string') {
    // 兼容旧调用方式（传account_id字符串）
    return `/api/proxy/image?url=${encodeURIComponent(`https://q.qlogo.cn/headimg_dl?dst_uin=${record}&spec=640&img_type=jpg`)}`
  }
  if (record?.platform === 'qq') {
    return `/api/proxy/image?url=${encodeURIComponent(`https://q.qlogo.cn/headimg_dl?dst_uin=${record.account_id}&spec=640&img_type=jpg`)}`
  }
  return ''
}

function showAddConfig() {
  editingConfig.value = null
  Object.assign(configForm, { name: '', api_base: '', api_key: '', model: '', embed_model: '', max_tokens: 4096, temperature: 7 })
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
      await aiConfigApi.updateConfig(editingConfig.value.id, { ...configForm })
    } else {
      await aiConfigApi.createConfig({ ...configForm })
    }
    Message.success('保存成功')
    configModalVisible.value = false
    loadAIConfigs()
  } catch (e: any) { Message.error('保存失败') }
  finally { configSaving.value = false }
}

async function activateConfig(id: number) {
  try {
    await aiConfigApi.activateConfig(id)
    Message.success('已激活')
    loadAIConfigs()
  } catch (e: any) {
    Message.error(getErrorMessage(e, '激活配置失败'))
  }
}

async function deleteConfig(id: number) {
  try {
    await aiConfigApi.deleteConfig(id)
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
    const { data } = await aiConfigApi.testConfig({ ...configForm })
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
    const { data } = await authApi.xhsSaveCookies()
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
    const { data } = await authApi.xhsResetBrowser()
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
    const { data } = await authApi.getXHSQrcode()
    qrcodeData.value = data.qrcode || ''
    if (!qrcodeData.value) { Message.warning('获取二维码失败'); xhsLogging.value = false; return }
    // 自动连接浏览器实时预览
    startBrowserPreview('xhs')
    loginStatusText.value = '请使用手机扫描二维码'
    loginStatusColor.value = ''
    // 轮询状态
    loginPollInterval = setInterval(async () => {
      try {
        const { data: status } = await authApi.getXHSStatus()
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
    const { data } = await authApi.refreshQQCookies()
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
    const { data } = await authApi.qqNapcatLogin()
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
    const { data } = await authApi.getQQQrcode()
    qrcodeData.value = data.qrcode || ''
    if (!qrcodeData.value) { Message.warning('获取QQ二维码失败'); qqLogging.value = false; return }
    // 自动连接浏览器实时预览
    startBrowserPreview('qq')
    loginStatusText.value = '请使用手机QQ扫描二维码'
    loginStatusColor.value = ''
    // 轮询状态
    loginPollInterval = setInterval(async () => {
      try {
        const { data: status } = await authApi.getQQStatus()
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
  loadSystemConfigs()
})

onUnmounted(() => {
  if (logRefreshTimer) { clearInterval(logRefreshTimer); logRefreshTimer = null }
  cleanupLoginPoll()
  stopBrowserPreview()
})
</script>
