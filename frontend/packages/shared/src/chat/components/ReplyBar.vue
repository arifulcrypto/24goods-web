<script setup lang="ts">
import { useI18n } from 'vue-i18n'

/** Composer bar for "replying to X" / "editing message" with a ✕ (shared by buyer and staff). */
defineProps<{ title: string; preview: string }>()
const emit = defineEmits<{ cancel: [] }>()
const { t } = useI18n()
</script>

<template>
  <div class="gc-replybar" role="status">
    <span class="gc-replybar-line" aria-hidden="true" />
    <span class="gc-replybar-main">
      <strong class="gc-replybar-title">{{ title }}</strong>
      <span class="gc-replybar-text">{{ preview }}</span>
    </span>
    <button type="button" class="gc-replybar-x" :aria-label="t('common.cancel')" @click="emit('cancel')">✕</button>
  </div>
</template>

<style scoped>
.gc-replybar { display: flex; align-items: center; gap: 8px; padding: 4px 8px; background: var(--gc-panel, #fff); border-top: 1px solid rgb(127 127 127 / 0.25); font-size: 13px }
.gc-replybar-line { align-self: stretch; width: 3px; border-radius: 2px; background: var(--gc-accent, #3b82f6) }
.gc-replybar-main { flex: 1; min-width: 0; display: flex; flex-direction: column }
.gc-replybar-title { color: var(--gc-accent, #3b82f6); font-size: 12px }
.gc-replybar-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 0.8 }
.gc-replybar-x { flex: none; width: 44px; height: 44px; border: 0; background: transparent; color: inherit; cursor: pointer; font-size: 16px }
</style>
