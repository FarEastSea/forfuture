<template>
  <div class="page-container chat-page">
    <aside class="chat-sidebar">
      <div class="chat-sidebar-header">
        <div class="chat-sidebar-copy">
          <span class="chat-section-kicker">Conversation pool</span>
          <h3>对话上下文</h3>
          <p>{{ sessionSummary }}</p>
        </div>

        <a-button long type="primary" @click="createSession" class="new-chat-btn">
          <template #icon><icon-plus /></template>
          新对话
        </a-button>
      </div>

      <div v-if="sessionsError" class="inline-state-card error compact">
        <div>
          <strong>会话加载失败</strong>
          <p>{{ sessionsError }}</p>
        </div>
        <a-button size="small" type="outline" @click="loadSessions()">
          <template #icon><icon-refresh /></template>
          重试
        </a-button>
      </div>

      <div v-else-if="!sessionsLoading && sessions.length === 0" class="inline-state-card compact">
        <div>
          <strong>还没有对话</strong>
          <p>先创建一个会话，再开始围绕记录提问。</p>
        </div>
        <a-button size="small" type="outline" @click="createSession">立即开始</a-button>
      </div>

      <a-spin :loading="sessionsLoading" class="chat-session-spin">
        <div class="session-list" :class="{ empty: !sessions.length }">
          <div
            v-for="session in sessions"
            :key="session.id"
            class="session-item"
            :class="{ active: session.id === currentSessionId }"
            @click="selectSession(session.id)"
          >
            <icon-message class="session-icon" />
            <div class="session-copy">
              <span class="session-title">{{ session.title || '新对话' }}</span>
              <span class="session-subtitle">{{ session.id === currentSessionId ? '当前上下文' : '点击继续对话' }}</span>
            </div>
            <icon-delete class="session-delete" @click.stop="deleteSession(session.id)" />
          </div>
        </div>
      </a-spin>
    </aside>

    <section class="chat-main">
      <div class="chat-panel-header">
        <div class="chat-panel-copy">
          <span class="chat-section-kicker">Insight console</span>
          <h3>{{ activeSessionTitle }}</h3>
          <p>{{ activeSessionDescription }}</p>
        </div>

        <div class="chat-panel-meta">
          <span class="chat-panel-badge">{{ sessionCountBadge }}</span>
          <span v-if="currentSessionId" class="chat-panel-badge subtle">{{ streaming ? 'AI 生成中' : '上下文已就绪' }}</span>
        </div>
      </div>

      <div class="messages-container" ref="messagesRef">
        <a-spin :loading="messagesLoading" style="width: 100%">
          <div v-if="messagesError" class="inline-state-card error">
            <div>
              <strong>会话内容暂时不可用</strong>
              <p>{{ messagesError }}</p>
            </div>
            <a-button size="small" type="outline" @click="selectSession(currentSessionId!)">
              <template #icon><icon-refresh /></template>
              重新加载
            </a-button>
          </div>

          <div v-else-if="!currentSessionId" class="chat-welcome">
            <icon-robot :size="48" />
            <h3>AI 知识库助手</h3>
            <p>把已采集的 QQ 空间和小红书记录转成可以追问的对话上下文。</p>
            <p class="sub-text">先从左侧挑一个会话，或直接新建对话开始分析。</p>
            <div class="welcome-examples">
              <p class="example-title">你可以这样问</p>
              <div class="example-list">
                <span class="example-item">她最近是不是遇到了什么困难？</span>
                <span class="example-item">她是个什么样的人？</span>
                <span class="example-item">她最近的情绪状态怎么样？</span>
                <span class="example-item">总结一下她最近的动态</span>
              </div>
            </div>
          </div>

          <div v-else-if="!messages.length && !streaming" class="inline-state-card empty">
            <div>
              <strong>这段对话还没有消息</strong>
              <p>发出第一个问题后，系统会基于已采集记录生成上下文回复。</p>
            </div>
          </div>

          <template v-else>
            <div
              v-for="message in messages"
              :key="message.id || message._tempId"
              class="message-wrap"
              :class="[message.role, { error: message.error }]"
            >
              <div class="message-bubble">
                <div class="message-content" v-html="renderMarkdown(message.content)"></div>
                <div class="message-sources" v-if="message.sources && message.sources.length">
                  <span class="source-label">引用来源：</span>
                  <a-tag
                    v-for="(source, index) in message.sources"
                    :key="index"
                    size="small"
                    :color="source.type === 'qq' ? 'blue' : 'red'"
                  >
                    {{ source.type === 'qq' ? 'QQ' : '小红书' }} #{{ source.id }}
                  </a-tag>
                </div>
              </div>
              <div class="message-time">{{ formatTime(message.created_at) }}</div>
            </div>

            <div v-if="streaming" class="message-wrap assistant">
              <div class="message-bubble streaming">
                <div class="message-content" v-html="renderMarkdown(streamContent)"></div>
                <span class="typing-cursor">|</span>
              </div>
            </div>
          </template>
        </a-spin>
      </div>

      <div class="chat-input-area" v-if="currentSessionId">
        <a-textarea
          v-model="inputText"
          placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
          :disabled="messagesLoading || streaming"
          :auto-size="{ minRows: 2, maxRows: 6 }"
          @keydown.enter.exact.prevent="sendMessage"
          class="chat-input"
        />
        <a-button type="primary" :loading="streaming" @click="sendMessage" class="send-btn">
          <icon-send />
          <span>发送</span>
        </a-button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { chatApi } from '@/api'
import { IconDelete, IconMessage, IconPlus, IconRefresh, IconRobot, IconSend } from '@arco-design/web-vue/es/icon'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ breaks: true, linkify: true })

const sessions = ref<any[]>([])
const currentSessionId = ref<number | null>(null)
const messages = ref<any[]>([])
const inputText = ref('')
const sessionsLoading = ref(false)
const sessionsError = ref('')
const messagesLoading = ref(false)
const messagesError = ref('')
const streaming = ref(false)
const streamContent = ref('')
const messagesRef = ref<HTMLElement | null>(null)

const activeSession = computed(() => {
  return sessions.value.find((session) => session.id === currentSessionId.value) || null
})

const activeSessionTitle = computed(() => {
  return activeSession.value?.title || '选择一段对话继续分析'
})

const activeSessionDescription = computed(() => {
  if (!currentSessionId.value) {
    return '从左侧挑一段已有会话，或新建对话开始提问。'
  }

  if (messagesLoading.value) {
    return '正在同步这段会话的上下文内容。'
  }

  if (streaming.value) {
    return 'AI 正在根据已采集记录生成回复。'
  }

  if (messages.value.length) {
    return `已载入 ${messages.value.length} 条消息，可以继续追问和深入判断。`
  }

  return '这段对话还没有消息，先抛出一个问题开始分析。'
})

const sessionSummary = computed(() => {
  if (!sessions.value.length) {
    return '创建首个会话后，就能把采集记录转成可持续追问的上下文。'
  }

  return `当前共有 ${sessions.value.length} 个会话，可以快速切换不同分析线程。`
})

const sessionCountBadge = computed(() => {
  return sessions.value.length ? `${sessions.value.length} 个会话` : '等待创建会话'
})

async function loadSessions(selectFirstSession = !currentSessionId.value) {
  sessionsLoading.value = true
  sessionsError.value = ''

  try {
    const { data } = await chatApi.getSessions()
    sessions.value = data || []

    if (selectFirstSession && sessions.value.length) {
      await selectSession(sessions.value[0].id)
    }
  } catch (error) {
    sessions.value = []
    sessionsError.value = getErrorMessage(error, '暂时无法读取对话列表，请稍后重试。')
  } finally {
    sessionsLoading.value = false
  }
}

async function createSession() {
  try {
    const { data } = await chatApi.createSession()
    sessions.value = [{ id: data.id, title: data.title }, ...sessions.value]
    currentSessionId.value = data.id
    messages.value = []
    messagesError.value = ''
    sessionsError.value = ''
  } catch (error) {
    Message.error(getErrorMessage(error, '创建对话失败，请稍后再试。'))
  }
}

async function selectSession(id: number) {
  currentSessionId.value = id
  messages.value = []
  messagesLoading.value = true
  messagesError.value = ''

  try {
    const { data } = await chatApi.getMessages(id)
    messages.value = data || []
    scrollToBottom()
  } catch (error) {
    messages.value = []
    messagesError.value = getErrorMessage(error, '加载会话失败，请稍后重试。')
    Message.error(messagesError.value)
  } finally {
    messagesLoading.value = false
  }
}

async function deleteSession(id: number) {
  try {
    await chatApi.deleteSession(id)
    const remainingSessions = sessions.value.filter((session) => session.id !== id)
    sessions.value = remainingSessions

    if (currentSessionId.value === id) {
      if (remainingSessions.length) {
        await selectSession(remainingSessions[0].id)
      } else {
        currentSessionId.value = null
        messages.value = []
        messagesError.value = ''
      }
    }
  } catch (error) {
    Message.error(getErrorMessage(error, '删除对话失败，请稍后再试。'))
  }
}

async function sendMessage(event?: Event) {
  if (event) {
    event.preventDefault()
  }

  const text = inputText.value.trim()
  if (!text || !currentSessionId.value || streaming.value) {
    return
  }

  inputText.value = ''
  messages.value = [
    ...messages.value,
    { _tempId: Date.now(), role: 'user', content: text, created_at: new Date().toISOString() },
  ]
  scrollToBottom()

  streaming.value = true
  streamContent.value = ''

  try {
    const response = await chatApi.sendMessage(currentSessionId.value, text)
    if (!response.body) {
      throw new Error('AI 未返回有效内容，请稍后再试。')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let streamErrorMessage = ''
    let shouldStop = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) {
          continue
        }

        const dataChunk = line.slice(6)
        if (dataChunk === '[DONE]') {
          shouldStop = true
          break
        }

        if (dataChunk.startsWith('[ERROR]')) {
          streamErrorMessage = dataChunk.replace('[ERROR]', '').trim() || '请求失败，请检查 AI 配置是否正确。'
          shouldStop = true
          break
        }

        if (dataChunk.startsWith('[CONTEXT_STATS]')) {
          continue
        }

        streamContent.value += dataChunk
        scrollToBottom()
      }

      if (shouldStop) {
        break
      }
    }

    if (streamErrorMessage) {
      throw new Error(streamErrorMessage)
    }

    const finalContent = streamContent.value.trim()
    if (!finalContent) {
      throw new Error('AI 未返回有效内容，请稍后再试。')
    }

    messages.value = [
      ...messages.value,
      {
        _tempId: Date.now() + 1,
        role: 'assistant',
        content: finalContent,
        created_at: new Date().toISOString(),
      },
    ]
    scrollToBottom()
  } catch (error) {
    const errorMessage = getErrorMessage(error, '请求失败，请检查 AI 配置是否正确。')
    messages.value = [
      ...messages.value,
      {
        _tempId: Date.now() + 1,
        role: 'assistant',
        content: errorMessage,
        created_at: new Date().toISOString(),
        error: true,
      },
    ]
    Message.error(errorMessage)
    scrollToBottom()
  } finally {
    streaming.value = false
    streamContent.value = ''
    sessions.value = sessions.value.map((session) => {
      if (session.id !== currentSessionId.value) {
        return session
      }

      if (session.title && session.title !== '新对话') {
        return session
      }

      return {
        ...session,
        title: text.slice(0, 30),
      }
    })
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

function renderMarkdown(text: string) {
  if (!text) {
    return ''
  }

  return md.render(text)
}

function formatTime(value: string | null) {
  if (!value) {
    return ''
  }

  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

onMounted(() => {
  void loadSessions()
})
</script>
