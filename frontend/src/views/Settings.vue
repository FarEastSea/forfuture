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
                      <a-button size="small" :type="getAccountToggleType(record)" @click="toggleAccount(record)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="确定删除？" @ok="removeAccount(record)">
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
                      <a-button size="small" :type="getAccountToggleType(record)" @click="toggleAccount(record)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="确定删除？" @ok="removeAccount(record)">
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
                      <a-button size="mini" :type="getAccountToggleType(record)" @click="toggleAccount(record)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="删除此登录账号？" @ok="removeAccount(record)">
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
                      <a-button size="mini" :type="getAccountToggleType(record)" @click="toggleAccount(record)">
                        {{ getAccountToggleLabel(record) }}
                      </a-button>
                      <a-popconfirm content="删除此登录账号？" @ok="removeAccount(record)">
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
        <div class="database-config-grid">
        <div class="settings-card infrastructure-card">
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

        <div class="settings-card infrastructure-card">
        <div class="settings-section">
          <h3>Redis 队列与实时事件</h3>
          <p class="section-desc">Redis 用于跨进程采集队列、限流和实时事件。地址中的密码不会在网页中回显。</p>
          <a-form :model="redisForm" layout="vertical" class="settings-form">
            <a-form-item label="启用 Redis">
              <a-switch v-model="redisForm.enabled" />
            </a-form-item>
            <a-form-item label="当前连接地址">
              <a-input :model-value="redisForm.displayUrl || '尚未配置'" readonly size="large" />
            </a-form-item>
            <a-form-item label="新的 Redis URL" help="留空则保留当前地址；格式如 redis://:密码@127.0.0.1:6379/0">
              <a-input-password
                v-model="redisForm.url"
                :disabled="!redisForm.enabled"
                autocomplete="new-password"
                placeholder="留空以保留当前配置"
                size="large"
              />
            </a-form-item>
            <a-space size="medium" wrap>
              <a-button type="primary" size="large" @click="testRedisConnection" :loading="redisTesting">测试连接</a-button>
              <a-button size="large" @click="saveRedisConfig" :loading="redisSaving">保存 Redis 配置</a-button>
            </a-space>
          </a-form>
          <div v-if="redisTestResult" class="db-test-result" :class="redisTestResult.success ? 'success' : 'error'">
            {{ redisTestResult.message }}
          </div>
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
          <a-slider v-model="configForm.temperature" :min="0" :max="2" :step="0.1" show-ticks />
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
import { IconPlus, IconEdit, IconDelete, IconScan, IconRefresh, IconCopy, IconLoading, IconEye, IconRobot, IconArrowRight, IconSave, IconUser, IconThunderbolt, IconSafe, IconStorage, IconSettings, IconFile } from '@arco-design/web-vue/es/icon'
import { useSettingsView } from './settings/useSettingsView'

const {
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
} = useSettingsView()
</script>
