<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bell, MuteNotification } from '@element-plus/icons-vue'
import { beep, ensureNotificationPermission, notificationPermission, type PermissionState } from '../notify'
import { usePrefsStore } from '../stores/prefs'

const { t } = useI18n()
const prefs = usePrefsStore()
const perm = ref<PermissionState>(notificationPermission())
const on = computed(() => prefs.sound || (prefs.notify && perm.value === 'granted'))

async function toggleDesktop(v: boolean | string | number) {
  if (v === true) {
    prefs.notify = await ensureNotificationPermission()
    perm.value = notificationPermission()
  } else {
    prefs.notify = false
  }
}
function toggleSound(v: boolean | string | number) {
  prefs.sound = v === true
  if (v === true) beep() // user gesture: also unlocks audio
}
</script>

<template>
  <el-popover trigger="click" :width="300" placement="bottom-end">
    <template #reference>
      <el-button text class="bell" :aria-label="t('notify.title')">
        <el-icon :size="18"><component :is="on ? Bell : MuteNotification" /></el-icon>
      </el-button>
    </template>
    <div class="np">
      <h3 class="np-h">{{ t('notify.title') }}</h3>
      <p class="np-sub muted">{{ t('notify.when') }}</p>
      <div class="np-row">
        <label for="np-sound">{{ t('notify.sound') }}</label>
        <el-switch id="np-sound" :model-value="prefs.sound" @update:model-value="toggleSound" />
      </div>
      <div class="np-row">
        <label for="np-desktop">{{ t('notify.desktop') }}</label>
        <el-switch id="np-desktop" :model-value="prefs.notify && perm === 'granted'" :disabled="perm === 'denied' || perm === 'unsupported'" @update:model-value="toggleDesktop" />
      </div>
      <el-alert v-if="perm === 'denied'" type="warning" :closable="false" show-icon :title="t('notify.denied')" />
      <el-alert v-else-if="perm === 'unsupported'" type="info" :closable="false" show-icon :title="t('notify.unsupported')" />
    </div>
  </el-popover>
</template>

<style scoped>
.np-h {
  margin: 0 0 4px;
  font-size: 14px;
}
.np-sub {
  margin: 0 0 8px;
  font-size: 12px;
}
.np-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 40px;
  font-size: 13px;
}
.bell {
  padding: 8px;
}
</style>
