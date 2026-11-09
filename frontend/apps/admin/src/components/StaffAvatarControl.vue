<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { User } from '@24goods/shared'
import { client } from '../api'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const busy = ref(false)

async function pick(ev: Event) {
  const input = ev.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f || busy.value) return
  if (!f.type.startsWith('image/') || f.size > 2 * 1024 * 1024) {
    ElMessage.warning(t('top.avatarType'))
    return
  }
  busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f, 'avatar')
    auth.user = await client.request<User>('PUT', '/me/avatar', { form: fd })
  } catch {
    ElMessage.error(t('top.avatarType'))
  } finally {
    busy.value = false
  }
}
async function remove() {
  busy.value = true
  try {
    auth.user = await client.delete<User>('/me/avatar')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="sac">
    <label class="sac-btn">
      {{ t('top.avatarChange') }}
      <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" :disabled="busy" @change="pick" />
    </label>
    <el-button v-if="auth.user?.avatar_url" text size="small" :disabled="busy" @click="remove">{{ t('top.avatarRemove') }}</el-button>
  </div>
</template>

<style scoped>
.sac {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px 8px;
  font-size: 13px;
}
.sac-btn {
  position: relative;
  cursor: pointer;
  color: var(--el-color-primary);
}
.sac-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
</style>
