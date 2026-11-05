<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { AgentStatusDot } from '@24goods/shared/chat'
import { useSupportStore } from '../stores/support'

const props = defineProps<{ showMessage?: boolean }>()
const emit = defineEmits<{ click: [] }>()
const { t } = useI18n()
const support = useSupportStore()
onMounted(() => void support.load())
const st = computed(() => support.status?.status ?? 'offline')
const msg = computed(() => (props.showMessage ? support.status?.message : null))
</script>

<template>
  <button type="button" class="pill" :class="`is-${st}`" @click="emit('click')">
    <AgentStatusDot :status="st" />
    <span class="pill-label">{{ t(`support.${st}`) }}</span>
    <span v-if="msg" class="pill-msg">· {{ msg }}</span>
  </button>
</template>

<style scoped>
.pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  border: 0;
  background: rgba(255, 255, 255, 0.85);
  color: var(--c-text);
  font-size: 12px;
  cursor: pointer;
  max-width: 100%;
  overflow: hidden;
}
/* expand the hit area to 44 px without a fat pill */
.pill::after {
  content: '';
  position: absolute;
  inset: -6px 0;
}
.pill-label {
  white-space: nowrap;
  font-weight: 600;
}
.pill-msg {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--c-text-2);
}
</style>
