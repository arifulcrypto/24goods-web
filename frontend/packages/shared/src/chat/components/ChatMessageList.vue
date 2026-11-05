<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { QuoteCardPayload } from '../../types'
import { formatChatTime } from '../../format'
import type { ChatItem } from '../useConversation'
import { needsTimeDivider } from '../merge'
import ChatMessage from './ChatMessage.vue'
import './chat.css'

const props = defineProps<{
  items: ChatItem[]
  mode: 'buyer' | 'staff'
  loading?: boolean
  loadingOlder?: boolean
  hasMore?: boolean
  busyQuoteId?: string | null
}>()
const emit = defineEmits<{
  reachTop: []
  preview: [url: string]
  openProduct: [id: string]
  openOrder: [id: string]
  acceptQuote: [q: QuoteCardPayload]
  withdrawQuote: [q: QuoteCardPayload]
  retry: [clientMsgId: string]
  discard: [clientMsgId: string]
}>()
const { t, locale } = useI18n()
const scroller = ref<HTMLElement | null>(null)

function createdAt(it: ChatItem): string {
  return it.kind === 'server' ? it.message.created_at : it.pending.created_at
}
function showTime(i: number): boolean {
  const cur = props.items[i]
  if (!cur) return false
  const prev = props.items[i - 1]
  return needsTimeDivider(prev ? { created_at: createdAt(prev) } : undefined, { created_at: createdAt(cur) })
}

function isNearBottom(threshold = 120): boolean {
  const el = scroller.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}
async function scrollToBottom(smooth = false): Promise<void> {
  await nextTick()
  const el = scroller.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}
/** Run `fn` (which prepends items) and keep the visible message in place. */
async function preserveScroll(fn: () => Promise<unknown>): Promise<void> {
  const el = scroller.value
  const before = el ? el.scrollHeight - el.scrollTop : 0
  await fn()
  await nextTick()
  if (el) el.scrollTop = el.scrollHeight - before
}

let topLock = false
function onScroll() {
  const el = scroller.value
  if (!el || topLock) return
  if (el.scrollTop < 60 && props.hasMore && !props.loadingOlder && !props.loading) {
    topLock = true
    emit('reachTop')
    setTimeout(() => (topLock = false), 300)
  }
}

defineExpose({ scrollToBottom, isNearBottom, preserveScroll, el: scroller })
</script>

<template>
  <div ref="scroller" class="gc-list" role="log" aria-live="polite" @scroll.passive="onScroll">
    <div class="gc-list-top">
      <span v-if="loadingOlder" class="gc-muted"><span class="gc-spin" /> {{ t('chat.loadingOlder') }}</span>
      <span v-else-if="!hasMore && items.length && !loading" class="gc-muted">{{ t('chat.noOlder') }}</span>
    </div>
    <slot v-if="loading && !items.length" name="loading">
      <div class="gc-skeleton" aria-hidden="true">
        <span /><span class="r" /><span />
      </div>
    </slot>
    <slot v-else-if="!items.length" name="empty" />
    <template v-for="(it, i) in items" :key="it.key">
      <div v-if="showTime(i)" class="gc-time">{{ formatChatTime(createdAt(it), locale) }}</div>
      <ChatMessage
        :item="it"
        :mode="mode"
        :busy-quote-id="busyQuoteId"
        @preview="emit('preview', $event)"
        @open-product="emit('openProduct', $event)"
        @open-order="emit('openOrder', $event)"
        @accept-quote="emit('acceptQuote', $event)"
        @withdraw-quote="emit('withdrawQuote', $event)"
        @retry="emit('retry', $event)"
        @discard="emit('discard', $event)"
      />
    </template>
    <slot name="bottom" />
  </div>
</template>
