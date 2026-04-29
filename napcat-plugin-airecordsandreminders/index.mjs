import WebSocket from 'ws';
import fs from 'fs';
import path from 'path';
import os from 'os';

// ===== WebSocket 客户端 =====
class WSClient {
  constructor(url, logger) {
    this.url = url;
    this.logger = logger;
    this.ws = null;
    this.reconnectDelay = 3000;
    this.maxReconnectDelay = 60000;
    this.currentDelay = this.reconnectDelay;
    this.callbacks = [];
    this._shouldReconnect = true;
    this._selfQQ = 'unknown';
    this._pingTimer = null;
    this._connectTimer = null;
    this._isConnecting = false;
  }

  async connect(selfQQ) {
    this._selfQQ = selfQQ;
    this._shouldReconnect = true;
    return this._doConnect();
  }

  /** 更新自身QQ号并重新发送认证 */
  updateSelfQQ(qq) {
    if (qq && qq !== 'unknown' && qq !== this._selfQQ) {
      const oldQQ = this._selfQQ;
      this._selfQQ = qq;
      if (this.send({ type: 'auth', qq: this._selfQQ })) {
        this.logger.info(`[AI-Monitor][WS] 已更新QQ号并重新认证: ${oldQQ} → ${qq}`);
      }
    }
  }

  _doConnect() {
    if (this._isConnecting) return Promise.resolve(false);
    this._isConnecting = true;

    return new Promise((resolve) => {
      try {
        // 清理旧连接
        if (this.ws) {
          try { this.ws.removeAllListeners(); this.ws.terminate(); } catch {}
          this.ws = null;
        }

        this.logger.info(`[AI-Monitor][WS] 正在连接: ${this.url}`);
        this.ws = new WebSocket(this.url, {
          handshakeTimeout: 10000,
          // 允许自签名证书
          rejectUnauthorized: false,
        });

        // 连接超时保护（10秒）
        this._connectTimer = setTimeout(() => {
          if (this.ws && this.ws.readyState !== WebSocket.OPEN) {
            this.logger.error('[AI-Monitor][WS] 连接超时(10s)，终止');
            try { this.ws.terminate(); } catch {}
            this._isConnecting = false;
            this._scheduleReconnect();
            resolve(false);
          }
        }, 10000);

        this.ws.on('open', () => {
          if (this._connectTimer) { clearTimeout(this._connectTimer); this._connectTimer = null; }
          this._isConnecting = false;
          this.logger.info(`[AI-Monitor][WS] 已连接到后端: ${this.url}`);
          this.currentDelay = this.reconnectDelay;
          // 发送认证
          this.send({ type: 'auth', qq: this._selfQQ });
          this._startPing();
          resolve(true);
        });

        this.ws.on('message', (data) => {
          try {
            const msg = JSON.parse(data.toString());
            if (msg.type === 'pong' || msg.type === 'ping' || msg.type === 'welcome') {
              // 收到服务端ping时回pong，welcome确认连接就绪
              if (msg.type === 'ping') this.send({ type: 'pong' });
              if (msg.type === 'welcome') this.logger.info('[AI-Monitor][WS] 收到服务端welcome确认');
              return;
            }
            for (const cb of this.callbacks) cb(msg);
          } catch (e) {
            this.logger.error(`[AI-Monitor][WS] 消息解析失败: ${e.message}`);
          }
        });

        this.ws.on('close', (code, reason) => {
          if (this._connectTimer) { clearTimeout(this._connectTimer); this._connectTimer = null; }
          this._isConnecting = false;
          this.logger.warn(`[AI-Monitor][WS] 连接断开 code=${code} reason=${reason ? reason.toString() : ''}`);
          this._stopPing();
          this._scheduleReconnect();
        });

        this.ws.on('error', (err) => {
          if (this._connectTimer) { clearTimeout(this._connectTimer); this._connectTimer = null; }
          this._isConnecting = false;
          this.logger.error(`[AI-Monitor][WS] 错误: ${err.message}`);
          // error 之后通常会紧跟 close，所以不在这里 reconnect
          resolve(false);
        });
      } catch (e) {
        if (this._connectTimer) { clearTimeout(this._connectTimer); this._connectTimer = null; }
        this._isConnecting = false;
        this.logger.error(`[AI-Monitor][WS] 创建连接异常: ${e.message}`);
        this._scheduleReconnect();
        resolve(false);
      }
    });
  }

  _scheduleReconnect() {
    if (!this._shouldReconnect) return;
    this.logger.info(`[AI-Monitor][WS] ${(this.currentDelay / 1000).toFixed(1)}秒后重连...`);
    setTimeout(() => {
      if (this._shouldReconnect) this._doConnect();
    }, this.currentDelay);
    this.currentDelay = Math.min(this.currentDelay * 1.5, this.maxReconnectDelay);
  }

  disconnect() {
    this._shouldReconnect = false;
    this._stopPing();
    if (this._connectTimer) { clearTimeout(this._connectTimer); this._connectTimer = null; }
    if (this.ws) {
      try { this.ws.removeAllListeners(); this.ws.close(1000, 'plugin_shutdown'); } catch {}
      this.ws = null;
    }
  }

  /** 每20秒发一次心跳ping，防止连接被中间件/OS超时断开 */
  _startPing() {
    this._stopPing();
    this._pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.send({ type: 'ping' });
      }
    }, 20000);
  }

  _stopPing() {
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null; }
  }

  send(data) {
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(data));
        return true;
      }
    } catch (e) {
      this.logger.error(`[AI-Monitor][WS] 发送失败: ${e.message}`);
    }
    return false;
  }

  onMessage(callback) { this.callbacks.push(callback); }
  get connected() { return this.ws && this.ws.readyState === WebSocket.OPEN; }
}

// ===== 渲染器 =====
const tempDir = path.join(os.tmpdir(), 'ai-monitor-render');
if (!fs.existsSync(tempDir)) fs.mkdirSync(tempDir, { recursive: true });

async function renderToImage(ctx, html, logger) {
  try {
    // 尝试使用 napcat-plugin-puppeteer
    const puppeteer = ctx.puppeteer || (ctx.service && ctx.service.puppeteer);
    if (puppeteer) {
      const page = await puppeteer.page();
      try {
        await page.setContent(html, { waitUntil: 'networkidle0' });
        await page.setViewport({ width: 440, height: 100, deviceScaleFactor: 2 });
        const body = await page.$('body');
        const box = await body.boundingBox();
        await page.setViewport({ width: Math.ceil(box.width), height: Math.ceil(box.height), deviceScaleFactor: 2 });
        const filepath = path.join(tempDir, `render_${Date.now()}.png`);
        await page.screenshot({ path: filepath, fullPage: true, type: 'png' });
        return filepath;
      } finally { await page.close(); }
    }
    logger.warn('[AI-Monitor] puppeteer不可用，无法渲染HTML');
    return null;
  } catch (e) {
    logger.error(`[AI-Monitor] 渲染失败: ${e.message}`);
    return null;
  }
}

// ===== 消息发送 =====
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function callOB11(ctx, action, params) {
  return await ctx.actions.call(action, params, ctx.adapterName, ctx.pluginManager.config);
}

async function sendText(ctx, qq, content) {
  await callOB11(ctx, 'send_private_msg', {
    user_id: Number(qq),
    message: [{ type: 'text', data: { text: content } }],
  });
}

async function sendImage(ctx, qq, urlOrPath) {
  let file;
  if (urlOrPath.startsWith('http') || urlOrPath.startsWith('base64://')) {
    file = urlOrPath;
  } else {
    file = `file://${urlOrPath}`;
  }
  await callOB11(ctx, 'send_private_msg', {
    user_id: Number(qq),
    message: [{ type: 'image', data: { file } }],
  });
}

async function sendVoice(ctx, qq, urlOrPath) {
  const file = urlOrPath.startsWith('http') ? urlOrPath : `file://${urlOrPath}`;
  await callOB11(ctx, 'send_private_msg', {
    user_id: Number(qq),
    message: [{ type: 'record', data: { file } }],
  });
}

async function sendFile(ctx, qq, urlOrPath, name) {
  try {
    await callOB11(ctx, 'upload_private_file', {
      user_id: Number(qq), file: urlOrPath, name: name || 'file',
    });
  } catch (e) {
    console.warn(`[AI-Monitor] 发送文件失败: ${e.message}`);
  }
}

// ===== 处理后端指令 =====
async function handleBackendMessage(ctx, wsClient, msg, logger) {
  const type = msg.type;
  try {
    if (type === 'send_message') {
      const { target_qq, messages } = msg.payload;
      if (!target_qq || !messages) return;
      for (const m of messages) {
        switch (m.type) {
          case 'text': await sendText(ctx, target_qq, m.content); break;
          case 'image': await sendImage(ctx, target_qq, m.url || m.path); break;
          case 'voice': await sendVoice(ctx, target_qq, m.url || m.path); break;
          case 'file': await sendFile(ctx, target_qq, m.url || m.path, m.name); break;
          case 'render_html':
            const imgPath = await renderToImage(ctx, m.html, logger);
            if (imgPath) await sendImage(ctx, target_qq, imgPath);
            break;
        }
        await sleep(500);
      }
      wsClient.send({ type: 'message_sent', payload: { target_qq, count: messages.length, success: true } });

    } else if (type === 'render_and_send') {
      const { target_qq, html, extra_messages } = msg.payload;
      const imgPath = await renderToImage(ctx, html, logger);
      if (imgPath) await sendImage(ctx, target_qq, imgPath);
      if (extra_messages && extra_messages.length > 0) {
        await handleBackendMessage(ctx, wsClient, { type: 'send_message', payload: { target_qq, messages: extra_messages } }, logger);
      }

    } else if (type === 'get_qzone_feeds') {
      const { qq, count } = msg.payload;
      wsClient.send({
        type: 'qzone_feeds',
        payload: { qq, feeds: [], error: 'NapCat不支持QQ空间API，请使用Playwright模式' },
      });

    } else if (type === 'get_qzone_cookies') {
      // 通过NapCat内部API获取QQ空间Cookie，供后端免扫码登录QZone
      try {
        const loginInfo = await callOB11(ctx, 'get_login_info', {});
        const cookiesResp = await callOB11(ctx, 'get_cookies', { domain: 'qzone.qq.com' });
        const credResp = await callOB11(ctx, 'get_credentials', { domain: 'qzone.qq.com' });
        wsClient.send({
          type: 'qzone_cookies_result',
          payload: {
            success: true,
            login_info: loginInfo || {},
            cookies: cookiesResp || {},
            credentials: credResp || {},
          },
        });
        logger.info(`[AI-Monitor] get_qzone_cookies 成功: QQ=${loginInfo?.user_id || '?'}`);
      } catch (e) {
        logger.error(`[AI-Monitor] get_qzone_cookies 失败: ${e.message}`);
        wsClient.send({
          type: 'qzone_cookies_result',
          payload: { success: false, error: e.message },
        });
      }
    }
  } catch (e) {
    logger.error(`[AI-Monitor] 处理指令失败 (${type}): ${e.message}`);
    wsClient.send({ type: 'error', payload: { command: type, error: e.message } });
  }
}

// ===== 插件全局状态 =====
const DEFAULT_CONFIG = {
  backendWsUrl: 'ws://127.0.0.1:18100/ws/napcat',
  enabled: true,
};

let currentConfig = { ...DEFAULT_CONFIG };
let wsClient = null;
let pluginCtx = null;

/** 通过 OneBot API 获取机器人QQ号（最可靠方式）*/
async function fetchSelfQQ(ctx, logger) {
  // 方式1: ctx.selfInfo（NapCat内置）
  try {
    if (ctx.selfInfo && ctx.selfInfo.uin) return String(ctx.selfInfo.uin);
  } catch {}
  // 方式2: get_login_info API（与aichat/auto-tasks插件一致）
  try {
    const res = await ctx.actions.call('get_login_info', {}, ctx.adapterName, ctx.pluginManager.config);
    if (res && res.user_id) return String(res.user_id);
  } catch (e) {
    logger.warn(`[AI-Monitor] get_login_info 失败: ${e.message}`);
  }
  // 方式3: 其他属性
  try {
    if (ctx.bot && ctx.bot.uin) return String(ctx.bot.uin);
    if (ctx.account) return String(ctx.account);
  } catch {}
  return 'unknown';
}

// ===== NapCat 4.x 插件标准导出 =====

async function plugin_init(ctx) {
  pluginCtx = ctx;
  const logger = ctx.logger || console;

  // 加载配置
  try {
    if (fs.existsSync(ctx.configPath)) {
      const raw = fs.readFileSync(ctx.configPath, 'utf-8');
      currentConfig = { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
    } else {
      const dir = path.dirname(ctx.configPath);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(ctx.configPath, JSON.stringify(currentConfig, null, 2), 'utf-8');
    }
  } catch (e) {
    logger.error(`[AI-Monitor] 加载配置失败: ${e.message}`);
  }

  if (!currentConfig.enabled) {
    logger.info('[AI-Monitor] 插件已禁用');
    return;
  }

  // 获取自身QQ号
  let selfQQ = await fetchSelfQQ(ctx, logger);
  logger.info(`[AI-Monitor] 初始QQ号: ${selfQQ}`);

  // 连接后端
  wsClient = new WSClient(currentConfig.backendWsUrl, logger);
  await wsClient.connect(selfQQ);
  wsClient.onMessage((msg) => handleBackendMessage(ctx, wsClient, msg, logger));

  // 如果QQ还是unknown，延迟重试
  if (selfQQ === 'unknown') {
    const retryDelays = [3000, 8000, 20000];
    for (const delay of retryDelays) {
      setTimeout(async () => {
        const qq = await fetchSelfQQ(ctx, logger);
        if (qq !== 'unknown' && wsClient) {
          wsClient.updateSelfQQ(qq);
          logger.info(`[AI-Monitor] 延迟${delay / 1000}s获取QQ号: ${qq}`);
        }
      }, delay);
    }
  }

  logger.info('[AI-Monitor] 插件已启动');
}

async function plugin_cleanup(ctx) {
  const logger = ctx?.logger || console;
  logger.info('[AI-Monitor] 插件正在关闭...');
  if (wsClient) {
    wsClient.disconnect();
    wsClient = null;
  }
}

async function plugin_get_config() {
  return currentConfig;
}

function plugin_on_config_change(ctx, _, key, value) {
  currentConfig[key] = value;
  try {
    const dir = path.dirname(ctx.configPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(ctx.configPath, JSON.stringify(currentConfig, null, 2), 'utf-8');
  } catch {}

  // 如果修改了后端地址，重连
  if (key === 'backendWsUrl' && wsClient) {
    wsClient.disconnect();
    const logger = ctx.logger || console;
    wsClient = new WSClient(value, logger);
    fetchSelfQQ(ctx, logger).then(selfQQ => {
      wsClient.connect(selfQQ);
      wsClient.onMessage((msg) => handleBackendMessage(ctx, wsClient, msg, logger));
    });
  }
}

// 配置UI
let plugin_config_ui = [];
function buildConfigUI(ctx) {
  const { NapCatConfig } = ctx;
  return NapCatConfig.combine(
    NapCatConfig.html('<h3>🤖 AI Records & Reminders</h3><p>与后端通信，实现AI监控推送</p>'),
    NapCatConfig.boolean('enabled', '启用插件', true, '是否启用此插件'),
    NapCatConfig.text('backendWsUrl', '后端WebSocket地址', DEFAULT_CONFIG.backendWsUrl, '后端服务的WebSocket地址'),
  );
}

// 事件转发：将QQ消息转发给后端
async function plugin_onevent(ctx, event) {
  if (!wsClient || !wsClient.connected) return;

  // 从事件中获取自身QQ号（最可靠方式）
  if (wsClient._selfQQ === 'unknown') {
    let selfQQ = 'unknown';
    // 优先从事件取
    if (event.self_id) selfQQ = String(event.self_id);
    // 再试 ctx
    if (selfQQ === 'unknown') {
      try { if (ctx.selfInfo && ctx.selfInfo.uin) selfQQ = String(ctx.selfInfo.uin); } catch {}
    }
    // 最后异步调 API（不等结果）
    if (selfQQ === 'unknown') {
      fetchSelfQQ(ctx, ctx.logger || console).then(qq => {
        if (qq !== 'unknown' && wsClient) wsClient.updateSelfQQ(qq);
      });
    } else {
      wsClient.updateSelfQQ(selfQQ);
    }
  }

  if (event.post_type !== 'message') return;

  const payload = {
    msg_type: event.message_type || 'unknown',
    from_qq: String(event.sender?.user_id || event.user_id || ''),
    nickname: event.sender?.nickname || '',
    group_id: event.group_id ? String(event.group_id) : undefined,
    content: event.raw_message || '',
    time: event.time || Math.floor(Date.now() / 1000),
  };

  wsClient.send({ type: 'message_received', payload });
}

export {
  plugin_init,
  plugin_cleanup,
  plugin_get_config,
  plugin_on_config_change,
  plugin_config_ui,
  plugin_onevent,
};
