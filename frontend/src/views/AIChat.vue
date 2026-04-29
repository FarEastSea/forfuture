<template>
  <div class="page-container chat-page">
    <div class="chat-sidebar">
      <a-button long type="primary" @click="createSession" class="new-chat-btn">
        <template #icon><icon-plus /></template>
        新对话
      </a-button>
      <div class="session-list">
        <div
          v-for="s in sessions" :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
          @click="selectSession(s.id)"
        >
          <icon-message class="session-icon" />
          <span class="session-title">{{ s.title || '新对话' }}</span>
          <icon-delete class="session-delete" @click.stop="deleteSession(s.id)" />
        </div>
      </div>
    </div>

    <div class="chat-main">
      <div class="messages-container" ref="messagesRef">
        <div v-if="!currentSessionId" class="chat-welcome">
          <icon-robot :size="48" />
          <h3>AI 知识库助手</h3>
          <p>基于你收集的QQ空间和小红书全部动态记录进行问答</p>
          <p class="sub-text">AI能看到所有已采集的动态，支持模糊提问和深度分析</p>
          <div class="welcome-examples">
            <p class="example-title">💡 你可以这样问：</p>
            <div class="example-list">
              <span class="example-item">她最近是不是遇到了什么困难？</span>
              <span class="example-item">她是个什么样的人？</span>
              <span class="example-item">她最近的情绪状态怎么样？</span>
              <span class="example-item">总结一下她最近的动态</span>
            </div>
          </div>
        </div>

        <div v-for="msg in messages" :key="msg.id || msg._tempId" class="message-wrap" :class="msg.role">
          <div class="message-bubble">
            <div class="message-content" v-html="renderMarkdown(msg.content)"></div>
            <div class="message-sources" v-if="msg.sources && msg.sources.length">
              <span class="source-label">引用来源：</span>
              <a-tag v-for="(src, i) in msg.sources" :key="i" size="small" :color="src.type === 'qq' ? 'blue' : 'red'">
                {{ src.type === 'qq' ? 'QQ' : '小红书' }} #{{ src.id }}
              </a-tag>
            </div>
          </div>
          <div class="message-time">{{ formatTime(msg.created_at) }}</div>
        </div>

        <div v-if="streaming" class="message-wrap assistant">
          <div class="message-bubble">
            <div class="message-content" v-html="renderMarkdown(streamContent)"></div>
            <span class="typing-cursor">|</span>
          </div>
        </div>
      </div>

      <div class="chat-input-area" v-if="currentSessionId">
        <a-textarea
          v-model="inputText"
          placeholder="输入你的问题..."
          :auto-size="{ minRows: 1, maxRows: 5 }"
          @keydown.enter.exact="sendMessage"
          class="chat-input"
        />
        <a-button type="primary" :loading="streaming" @click="sendMessage" class="send-btn">
          <icon-send />
        </a-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { chatApi } from '@/api'
import { IconPlus, IconMessage, IconDelete, IconRobot, IconSend } from '@arco-design/web-vue/es/icon'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ breaks: true, linkify: true })

const sessions = ref<any[]>([])
const currentSessionId = ref<number | null>(null)
const messages = ref<any[]>([])
const inputText = ref('')
const streaming = ref(false)
const streamContent = ref('')
const messagesRef = ref<HTMLElement>()

async function loadSessions() {
  try {
    const { data } = await chatApi.getSessions()
    sessions.value = data || []
  } catch {}
}

async function createSession() {
  try {
    const { data } = await chatApi.createSession()
    sessions.value.unshift({ id: data.id, title: data.title })
    currentSessionId.value = data.id
    messages.value = []
  } catch {}
}

async function selectSession(id: number) {
  currentSessionId.value = id
  try {
    const { data } = await chatApi.getMessages(id)
    messages.value = data || []
    scrollToBottom()
  } catch {}
}

async function deleteSession(id: number) {
  try {
    await chatApi.deleteSession(id)
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
    }
  } catch {}
}

async function sendMessage(e?: Event) {
  if (e) e.preventDefault()
  const text = inputText.value.trim()
  if (!text || !currentSessionId.value || streaming.value) return

  inputText.value = ''
  messages.value.push({ _tempId: Date.now(), role: 'user', content: text, created_at: new Date().toISOString() })
  scrollToBottom()

  streaming.value = true
  streamContent.value = ''

  try {
    const resp = await chatApi.sendMessage(currentSessionId.value, text)
    if (!resp.body) return

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data === '[DONE]') break
          if (data.startsWith('[ERROR]')) {
            streamContent.value += data
            break
          }
          if (data.startsWith('[CONTEXT_STATS]')) {
            // 上下文统计信息，静默处理
            continue
          }
          streamContent.value += data
          scrollToBottom()
        }
      }
    }

    messages.value.push({
      _tempId: Date.now() + 1, role: 'assistant',
      content: streamContent.value, created_at: new Date().toISOString(),
    })
  } catch (e) {
    messages.value.push({
      _tempId: Date.now() + 1, role: 'assistant',
      content: '请求失败，请检查AI配置是否正确', created_at: new Date().toISOString(),
    })
  } finally {
    streaming.value = false
    streamContent.value = ''
    // 更新会话标题
    const sess = sessions.value.find(s => s.id === currentSessionId.value)
    if (sess && (sess.title === '新对话' || !sess.title)) {
      sess.title = text.slice(0, 30)
    }
  }
}

function renderMarkdown(text: string) {
  if (!text) return ''
  return md.render(text)
}

function formatTime(t: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

onMounted(() => { loadSessions() })
</script>
