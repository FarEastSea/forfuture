<template>
  <div class="page-container">
    <div class="page-header">
      <h2>定时任务</h2>
      <a-button type="primary" @click="showCreateModal">
        <template #icon><icon-plus /></template>
        新建任务
      </a-button>
    </div>

    <div v-if="taskError" class="inline-state-card error compact">
      <div>
        <strong>任务列表加载失败</strong>
        <p>{{ taskError }}</p>
      </div>
      <a-button size="small" type="outline" @click="loadTasks">重新加载</a-button>
    </div>

    <a-spin :loading="loading" style="width:100%">
      <div v-if="tasks.length === 0 && !loading && !taskError" class="empty-state">
        <icon-clock-circle :size="48" />
        <p>暂无定时任务</p>
        <p class="sub-text">创建任务后，AI会定时分析记录并在满足条件时通过QQ通知你</p>
        <a-button type="outline" @click="showCreateModal">创建第一个任务</a-button>
      </div>

      <div v-for="task in tasks" :key="task.id" class="task-card">
        <div class="task-header">
          <div class="task-title-row">
            <a-switch v-model="task.is_active" @change="toggleTask(task)" size="small" />
            <span class="task-name">{{ task.name }}</span>
            <a-tag :color="task.is_active ? 'green' : 'gray'" size="small">
              {{ task.is_active ? '运行中' : '已暂停' }}
            </a-tag>
          </div>
          <div class="task-actions">
            <a-button size="mini" @click="runNow(task)"><icon-play-arrow /> 立即执行</a-button>
            <a-button size="mini" @click="editTask(task)"><icon-edit /></a-button>
            <a-popconfirm content="确定删除此任务？" @ok="deleteTask(task.id)">
              <a-button size="mini" status="danger"><icon-delete /></a-button>
            </a-popconfirm>
          </div>
        </div>
        <div class="task-desc">{{ task.description }}</div>
        <div class="task-meta">
          <span>目标QQ: {{ task.target_qq }}</span>
          <span>Cron: {{ task.cron_expr || `每${task.interval_minutes}分钟` }}</span>
          <span>已执行: {{ task.run_count }}次</span>
          <span>已触发: {{ task.trigger_count }}次</span>
          <span v-if="task.last_run">上次执行: {{ formatTime(task.last_run) }}</span>
        </div>
        <a-collapse :bordered="false" class="task-logs-collapse" @change="handleLogsExpand(task.id, $event)">
          <a-collapse-item header="执行日志" :key="task.id">
            <a-spin :loading="Boolean(logLoadingMap[task.id])" style="width:100%">
              <div v-if="taskLogs[task.id]">
                <div v-for="log in taskLogs[task.id]" :key="log.id" class="log-item">
                  <span class="log-time">{{ formatTime(log.run_at) }}</span>
                  <a-tag :color="log.triggered ? 'orange' : 'gray'" size="small">
                    {{ log.triggered ? '已触发' : '静默' }}
                  </a-tag>
                  <span class="log-reason">{{ log.ai_reason }}</span>
                  <span v-if="log.error" class="log-error">{{ log.error }}</span>
                </div>
                <div v-if="taskLogs[task.id].length === 0" class="sub-text">暂无日志</div>
              </div>
              <div v-else class="sub-text">展开后会自动加载最近执行日志</div>
            </a-spin>
          </a-collapse-item>
        </a-collapse>
      </div>
    </a-spin>

    <a-modal v-model:visible="modalVisible" :title="editingTask ? '编辑任务' : '新建任务'" @ok="saveTask" :ok-loading="saving">
      <a-form :model="form" layout="vertical">
        <a-form-item label="任务名称" required>
          <a-input v-model="form.name" placeholder="如：关注她的状态" />
        </a-form-item>
        <a-form-item label="任务描述（自然语言）" required>
          <a-textarea v-model="form.description" placeholder="如：当她生病了或者遇到什么很大的困难时告诉我" :auto-size="{ minRows: 3 }" />
        </a-form-item>
        <a-form-item label="通知QQ号" required>
          <a-input v-model="form.target_qq" placeholder="触发时发送通知的QQ号" />
        </a-form-item>
        <a-form-item label="执行频率">
          <a-radio-group v-model="freqMode">
            <a-radio value="auto">AI自动判断</a-radio>
            <a-radio value="cron">Cron表达式</a-radio>
            <a-radio value="interval">固定间隔</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item v-if="freqMode === 'cron'" label="Cron表达式">
          <a-input v-model="form.cron_expr" placeholder="如 0 */2 * * *（每2小时）" />
        </a-form-item>
        <a-form-item v-if="freqMode === 'interval'" label="间隔（分钟）">
          <a-input-number v-model="form.interval_minutes" :min="5" :max="1440" />
        </a-form-item>
        <a-form-item label="消息格式偏好">
          <a-select v-model="form.message_format">
            <a-option value="text_image_voice">文字+图片+语音</a-option>
            <a-option value="text_image">文字+图片</a-option>
            <a-option value="text_voice">文字+语音</a-option>
            <a-option value="text">纯文字</a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { taskApi } from '@/api'
import { Message } from '@arco-design/web-vue'
import {
  IconPlus, IconClockCircle, IconPlayArrow, IconEdit, IconDelete
} from '@arco-design/web-vue/es/icon'

const tasks = ref<any[]>([])
const taskLogs = ref<Record<number, any[]>>({})
const logLoadingMap = ref<Record<number, boolean>>({})
const loading = ref(false)
const modalVisible = ref(false)
const saving = ref(false)
const editingTask = ref<any>(null)
const freqMode = ref('auto')
const taskError = ref('')

const form = reactive({
  name: '', description: '', target_qq: '',
  cron_expr: '', interval_minutes: 120, message_format: 'text_image',
})

async function loadTasks() {
  loading.value = true
  taskError.value = ''
  try {
    const { data } = await taskApi.getTasks()
    tasks.value = data || []
  } catch (error) {
    tasks.value = []
    taskError.value = getErrorMessage(error, '暂时无法读取任务列表，请稍后重试。')
  } finally { loading.value = false }
}

function showCreateModal() {
  editingTask.value = null
  Object.assign(form, { name: '', description: '', target_qq: '', cron_expr: '', interval_minutes: 120, message_format: 'text_image' })
  freqMode.value = 'auto'
  modalVisible.value = true
}

function editTask(task: any) {
  editingTask.value = task
  Object.assign(form, {
    name: task.name, description: task.description, target_qq: task.target_qq,
    cron_expr: task.cron_expr || '', interval_minutes: task.interval_minutes || 120,
    message_format: task.message_format || 'text_image',
  })
  freqMode.value = task.cron_expr ? 'cron' : task.interval_minutes ? 'interval' : 'auto'
  modalVisible.value = true
}

async function saveTask() {
  if (!form.name || !form.description || !form.target_qq) {
    Message.warning('请填写必填项'); return
  }
  if (freqMode.value === 'cron' && !form.cron_expr.trim()) {
    Message.warning('请输入有效的 Cron 表达式'); return
  }
  if (freqMode.value === 'interval' && (!form.interval_minutes || form.interval_minutes < 5)) {
    Message.warning('固定间隔至少为 5 分钟'); return
  }
  saving.value = true
  const payload = {
    ...form,
    cron_expr: freqMode.value === 'cron' ? form.cron_expr : null,
    interval_minutes: freqMode.value === 'interval' ? form.interval_minutes : null,
  }
  try {
    if (editingTask.value) {
      await taskApi.updateTask(editingTask.value.id, payload)
      Message.success('更新成功')
    } else {
      await taskApi.createTask(payload)
      Message.success('创建成功')
    }
    modalVisible.value = false
    loadTasks()
  } catch (e: any) {
    Message.error(e.response?.data?.detail || '操作失败')
  } finally { saving.value = false }
}

async function toggleTask(task: any) {
  try {
    await taskApi.toggleTask(task.id)
  } catch (error) {
    task.is_active = !task.is_active
    Message.error(getErrorMessage(error, '切换任务状态失败，请稍后重试。'))
  }
}

async function deleteTask(id: number) {
  try {
    await taskApi.deleteTask(id)
    Message.success('任务已删除')
    loadTasks()
  } catch (error) {
    Message.error(getErrorMessage(error, '删除任务失败，请稍后重试。'))
  }
}

async function runNow(task: any) {
  try {
    await taskApi.runNow(task.id)
    Message.success('任务已触发执行')
  } catch (e: any) { Message.error(e.response?.data?.detail || '执行失败') }
}

async function loadLogs(taskId: number) {
  logLoadingMap.value = {
    ...logLoadingMap.value,
    [taskId]: true,
  }
  try {
    const { data } = await taskApi.getLogs(taskId)
    taskLogs.value = {
      ...taskLogs.value,
      [taskId]: data || [],
    }
  } catch (error) {
    Message.error(getErrorMessage(error, '任务日志加载失败，请稍后重试。'))
  } finally {
    logLoadingMap.value = {
      ...logLoadingMap.value,
      [taskId]: false,
    }
  }
}

function handleLogsExpand(taskId: number, activeKeys: string | number | Array<string | number>) {
  const isExpanded = Array.isArray(activeKeys) ? activeKeys.includes(taskId) : activeKeys === taskId
  if (isExpanded && !taskLogs.value[taskId] && !logLoadingMap.value[taskId]) {
    void loadLogs(taskId)
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

function formatTime(t: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(() => { loadTasks() })
</script>
