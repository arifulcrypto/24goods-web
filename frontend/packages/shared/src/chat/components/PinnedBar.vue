<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Message } from '../../types'

/** Telegram-style pinned bar: newest pin + count; tapping jumps to it and cycles to the next. */
const props = defineProps<{ pins: Message[] }>()
const emit = defineEmits<{ jump: [id: number] }>()
const { t } = useI18n()
const idx = ref(0)
watch(
  () => props.pins.map((p) => p.id).join(','),
  () => {
    idx.value = 0
  },
)
const cur = computed(() => props.pins[idx.value] ?? props.pins[0] ?? null)
const label = computed(() => {
  const m = cur.value
  if (!m) return ''
  if (m.type === 'text') return (m.text ?? '').replace(/\s+/g, ' ').slice(0, 120)
  return t(`chat.preview.${m.type}`)
})
function tap() {
  const m = cur.value
  if (!m) return
  emit('jump', m.id)
  idx.value = (idx.value + 1) % props.pins.length
}
</script>

<template>
  <button v-if="cur" type="button" class="gc-pinbar" :aria-label="t('chatActions.pinnedBar')" @click="tap">
    <span class="gc-pinbar-line" aria-hidden="true" />
    <span class="gc-pinbar-main">
      <strong class="gc-pinbar-title">{{ t('chatActions.pinnedTitle') }}<template v-if="pins.length > 1"> #{{ (idx % pins.length) + 1 }}/{{ pins.length }}</template></strong>
      <span class="gc-pinbar-text">{{ label }}</span>
    </span>
    <span aria-hidden="true">📌</span>
  </button>
</template>

<style scoped>
.gc-pinbar { position: sticky; top: 0; z-index: 4; display: flex; align-items: center; gap: 8px; width: 100%; min-height: 44px; padding: 4px 12px; border: 0; border-bottom: 1px solid rgb(127 127 127 / 0.25); background: var(--gc-panel, #fff); color: inherit; font: inherit; font-size: 13px; text-align: start; cursor: pointer; box-sizing: border-box }
.gc-pinbar-line { align-self: stretch; width: 3px; border-radius: 2px; background: var(--gc-accent, #3b82f6) }
.gc-pinbar-main { flex: 1; min-width: 0; display: flex; flex-direction: column }
.gc-pinbar-title { color: var(--gc-accent, #3b82f6); font-size: 12px }
.gc-pinbar-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
</style>
