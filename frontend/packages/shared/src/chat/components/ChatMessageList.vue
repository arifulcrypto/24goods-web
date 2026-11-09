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
  /** Show a "View order" button next to order-number chips (buyer app). */
  selfId?: string
  /** Own display name / avatar for pending (not yet echoed) messages. */
  selfName?: string
  selfAvatar?: string | null
  viewOrder?: boolean
}>()
const emit = defineEmits<{
  action: [action: 'recall' | 'pin' | 'reply', id: number]
  reachTop: []
  preview: [url: string]
  openProduct: [id: string]
  buyProduct: [id: string]
  openOrderNo: [orderNo: string]
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

// In-app plain-text viewer for `file` bubbles (rendered as text in a <pre>, never as HTML).
const viewer = ref<{ name: string; url: string; text: string; state: 'loading' | 'ready' | 'error' } | null>(null)
async function openFile(f: { url: string; name: string }) {
  const cur = { name: f.name, url: f.url, text: '', state: 'loading' as 'loading' | 'ready' | 'error' }
  viewer.value = cur
  try {
    const r = await fetch(f.url, { credentials: 'same-origin' })
    if (!r.ok) throw new Error(String(r.status))
    const text = (await r.text()).slice(0, 300_000)
    if (viewer.value === cur) viewer.value = { ...cur, text, state: 'ready' }
  } catch {
    if (viewer.value === cur) viewer.value = { ...cur, state: 'error' }
  }
}

defineExpose({ scrollToBottom, isNearBottom, preserveScroll, el: scroller })
</script>

<template>
  <div ref="scroller" class="gc-list" role="log" aria-live="polite" @scroll.passive="onScroll">
    <div v-for="pin in items.filter(i => i.kind === 'server' && (i.message.payload as {pinned?: boolean} | null)?.pinned)" :key="`pin-${pin.key}`" class="gc-pinned" role="note">📌 {{ pin.kind === 'server' ? (pin.message.text?.slice(0, 160) || t('chatActions.media')) : '' }}</div>
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
        :self-id="selfId"
        :self-name="selfName"
        :self-avatar="selfAvatar"
        @action="(a, id) => emit('action', a, id)"
        :mode="mode"
        :busy-quote-id="busyQuoteId"
        :view-order="viewOrder"
        @preview="emit('preview', $event)"
        @open-file="openFile"
        @open-product="emit('openProduct', $event)"
        @buy-product="emit('buyProduct', $event)"
        @open-order-no="emit('openOrderNo', $event)"
        @open-order="emit('openOrder', $event)"
        @accept-quote="emit('acceptQuote', $event)"
        @withdraw-quote="emit('withdrawQuote', $event)"
        @retry="emit('retry', $event)"
        @discard="emit('discard', $event)"
      />
      <slot name="item-extra" :item="it" />
    </template>
    <slot name="bottom" />
    <Teleport to="body">
      <div v-if="viewer" class="gc-viewer" role="dialog" aria-modal="true" :aria-label="viewer.name" @click.self="viewer = null">
        <div class="gc-viewer-box">
          <div class="gc-viewer-head">
            <strong>{{ viewer.name }}</strong>
            <a v-if="viewer.state === 'ready'" class="gc-btn" :href="viewer.url" :download="viewer.name" rel="noopener">{{ t('chat.file.download') }}</a>
            <button type="button" class="gc-btn" @click="viewer = null">{{ t('chat.file.close') }}</button>
          </div>
          <pre v-if="viewer.state === 'ready'" class="gc-viewer-text">{{ viewer.text }}</pre>
          <p v-else-if="viewer.state === 'error'" class="gc-viewer-text">{{ t('chat.file.loadFailed') }}</p>
          <p v-else class="gc-viewer-text">{{ t('common.loading') }}</p>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>.gc-pinned{max-width:100%;overflow-wrap:anywhere;padding:8px 12px;border-bottom:1px solid #8885;box-sizing:border-box}</style>
