<script setup lang="ts">
/**
 * Renders linkify() segments as Vue nodes (never v-html). Chips: URL (confirm → open), email /
 * order no. / number / code (copy + specific toast). UI-neutral: dialog + toast come from the
 * injected SmartTextAdapter.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { copyText } from '../../util'
import { linkify, type Segment } from '../linkify'
import { openExternal, useSmartTextAdapter } from '../smartText'

const props = defineProps<{ text: string | null | undefined; code?: boolean; viewOrder?: boolean }>()
const emit = defineEmits<{ openOrderNo: [orderNo: string] }>()
const { t } = useI18n()
const adapter = useSmartTextAdapter()

const segments = computed(() => linkify(props.text, { code: props.code }))

async function copy(seg: Segment, toastKey: string) {
  const value = seg.kind === 'number' ? seg.text.replace(/[\s-]/g, '') : seg.text
  const ok = await copyText(value)
  adapter.toast(ok ? t(toastKey) : t('common.copyFailed'))
}
async function openUrl(seg: Segment) {
  if (!seg.href) return
  if (await adapter.confirmOpenUrl(seg.href)) openExternal(seg.href)
}
function label(seg: Segment): string {
  if (seg.kind === 'url') return seg.host ?? seg.text
  if (seg.kind === 'email') return t('chat.smart.email')
  if (seg.kind === 'order_no') return t('chat.smart.orderNo')
  if (seg.kind === 'number') return t('chat.smart.number')
  return seg.text
}
</script>

<template>
  <span class="gc-smart">
    <template v-for="(seg, i) in segments" :key="i">
      <template v-if="seg.kind === 'text'">{{ seg.text }}</template>
      <template v-else-if="seg.kind === 'url'"
        ><button type="button" class="gc-chip gc-chip-url" :title="seg.href" @click="openUrl(seg)"
          ><span aria-hidden="true">🔗</span> {{ label(seg) }}</button
        ></template
      >
      <template v-else-if="seg.kind === 'code'"
        ><button type="button" class="gc-chip gc-chip-code" @click="copy(seg, 'chat.smart.copiedCode')">{{ seg.text }}</button></template
      >
      <template v-else
        ><button
          type="button"
          class="gc-chip"
          :class="`gc-chip-${seg.kind}`"
          :title="seg.text"
          @click="copy(seg, `chat.smart.copied_${seg.kind}`)"
          ><span aria-hidden="true">{{ seg.kind === 'email' ? '✉' : seg.kind === 'order_no' ? '🧾' : '#' }}</span>
          {{ seg.text }}</button
        ><button
          v-if="seg.kind === 'order_no' && viewOrder"
          type="button"
          class="gc-chip gc-chip-view"
          @click="emit('openOrderNo', seg.text)"
          >{{ t('chat.order.view') }}</button
        ></template
      >
    </template>
  </span>
</template>
