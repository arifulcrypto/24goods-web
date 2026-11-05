<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AgentStatus, AgentStatusValue } from '@24goods/shared'
import { AgentStatusDot } from '@24goods/shared/chat'
import { api } from '../api'
import { toastError, toastOk } from '../ui'

const { t } = useI18n()
const STATUSES: AgentStatusValue[] = ['online', 'away', 'on_hold', 'offline']
const current = ref<AgentStatus | null>(null)
const draftStatus = ref<AgentStatusValue>('offline')
const draftMessage = ref('')
const open = ref(false)
const saving = ref(false)

onMounted(async () => {
  try {
    current.value = await api.agent.getStatus()
    draftStatus.value = current.value.status
    draftMessage.value = current.value.message ?? ''
  } catch {
    /* shown as offline */
  }
})

async function save() {
  saving.value = true
  try {
    current.value = await api.agent.setStatus(draftStatus.value, draftMessage.value.trim() || null)
    toastOk(t('top.statusUpdated'))
    open.value = false
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function quick(s: AgentStatusValue) {
  draftStatus.value = s
  await save()
}
</script>

<template>
  <el-popover v-model:visible="open" placement="bottom-end" :width="300" trigger="click">
    <template #reference>
      <el-button class="status-btn" text bg>
        <AgentStatusDot :status="current?.status ?? 'offline'" />
        <span class="status-label">{{ t(`agentStatus.${current?.status ?? 'offline'}`) }}</span>
        <span v-if="current?.message" class="status-msg muted">· {{ current.message }}</span>
      </el-button>
    </template>
    <div class="status-pop">
      <div class="pop-title">{{ t('top.myStatus') }}</div>
      <div class="status-grid">
        <el-button
          v-for="s in STATUSES"
          :key="s"
          :type="draftStatus === s ? 'primary' : 'default'"
          plain
          size="small"
          @click="draftStatus = s"
          @dblclick="quick(s)"
        >
          <AgentStatusDot :status="s" />&nbsp;{{ t(`agentStatus.${s}`) }}
        </el-button>
      </div>
      <el-input
        v-model="draftMessage"
        :placeholder="t('top.statusMessagePlaceholder')"
        maxlength="120"
        show-word-limit
        clearable
        :aria-label="t('top.statusMessage')"
      />
      <div class="pop-hint muted">{{ t('top.statusMessage') }}</div>
      <el-button type="primary" :loading="saving" style="width: 100%" @click="save">{{ t('top.update') }}</el-button>
    </div>
  </el-popover>
</template>

<style scoped>
.status-btn {
  max-width: 280px;
}
.status-label {
  margin-left: 6px;
}
.status-msg {
  margin-left: 4px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-pop {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pop-title {
  font-weight: 600;
}
.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.status-grid .el-button {
  margin: 0;
}
.pop-hint {
  font-size: 12px;
  margin-top: -6px;
}
</style>
