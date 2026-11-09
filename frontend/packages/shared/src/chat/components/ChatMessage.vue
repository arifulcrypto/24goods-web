<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { previewImageUrl } from '../../media/preferences'
import { copyText } from '../../util'
import { nowTick } from './tick'
import { useI18n } from 'vue-i18n'
import type { FilePayload, OrderCardPayload, ProductCardPayload, QuoteCardPayload, SystemPayload } from '../../types'
import { formatBytes, formatChatTime, formatMoney, formatRelative } from '../../format'
import { safeAssetUrl } from '../../api/endpoints'
import type { ChatItem } from '../useConversation'
import VoiceBubble from './VoiceBubble.vue'
import QuoteCardView from './QuoteCardView.vue'
import OrderCardView from './OrderCardView.vue'
import ProductCardView from './ProductCardView.vue'
import SmartText from './SmartText.vue'

const props = defineProps<{ item: ChatItem; mode: 'buyer' | 'staff'; busyQuoteId?: string | null; viewOrder?: boolean; selfId?: string; selfName?: string; selfAvatar?: string | null }>()
const emit = defineEmits<{
  preview: [url: string]
  openFile: [f: { url: string; name: string }]
  openProduct: [id: string]
  buyProduct: [id: string]
  openOrderNo: [orderNo: string]
  openOrder: [id: string]
  acceptQuote: [q: QuoteCardPayload]
  withdrawQuote: [q: QuoteCardPayload]
  retry: [clientMsgId: string]
  action: [action: 'recall' | 'pin' | 'reply', id: number]
  discard: [clientMsgId: string]
}>()
const { t, te, locale } = useI18n()

/** Normalized view of a server or pending item. */
const view = computed(() => {
  const it = props.item
  if (it.kind === 'pending') {
    const p = it.pending
    return {
      type: p.type,
      text: p.text,
      senderRole: it.senderRole,
      senderName: props.selfName ?? '',
      senderAvatar: props.selfAvatar ?? null,
      imageUrl: p.type === 'image' ? (safeAssetUrl(p.attachment?.url) ?? p.objectUrl) : null,
      voiceUrl: p.type === 'voice' ? (safeAssetUrl(p.attachment?.url) ?? p.objectUrl) : null,
      durationMs: p.durationMs ?? 0,
      width: p.width,
      height: p.height,
      payload: p.type === 'file' ? ({ name: p.filename ?? 'message.txt', bytes: p.blob?.size ?? 0, preview: p.text ?? '' } as FilePayload) : null,
      fileUrl: null as string | null,
      createdAt: p.created_at,
      state: p.state,
      clientMsgId: p.client_msg_id,
    }
  }
  const m = it.message
  return {
    type: m.type,
    text: m.text,
    senderRole: m.sender.role,
    senderName: m.sender.display_name,
    senderAvatar: m.sender.avatar_url ?? null,
    imageUrl: m.type === 'image' ? safeAssetUrl(m.attachment?.url) : null,
    voiceUrl: m.type === 'voice' ? safeAssetUrl(m.attachment?.url) : null,
    durationMs: m.attachment?.duration_ms ?? 0,
    width: m.attachment?.width,
    height: m.attachment?.height,
    payload: m.payload,
    createdAt: m.created_at,
    fileUrl: m.type === 'file' ? safeAssetUrl(m.attachment?.url) : null,
    state: 'sent' as const,
    clientMsgId: m.client_msg_id,
  }
})

/** The author deleted their account: the server blanks the content and sets payload.redacted. */
const redacted = computed(
  () => (view.value.payload as { redacted?: boolean } | null)?.redacted === true && view.value.type !== 'system',
)

const self = computed(() =>
  props.mode === 'buyer' ? view.value.senderRole === 'buyer' : view.value.senderRole === 'agent',
)
const centered = computed(() => view.value.type === 'system')
const file = computed(() => (view.value.type === 'file' ? (view.value.payload as FilePayload | null) : null))
const isCard = computed(() => ['product_card', 'quote_card', 'order_card'].includes(view.value.type))

const avatarSrc = computed(() => safeAssetUrl(view.value.senderAvatar))
const initial = computed(() => (view.value.senderName || '?').trim().charAt(0).toUpperCase() || '?')

/** Fixed-aspect image box (max 200×200) so the list does not jump while images load. */
const imgBox = computed(() => {
  const w = view.value.width
  const h = view.value.height
  const max = 200
  if (!w || !h) return { width: '160px', height: '160px' }
  const r = Math.min(max / w, max / h, 1)
  return { width: `${Math.max(60, Math.round(w * r))}px`, height: `${Math.max(60, Math.round(h * r))}px` }
})

const menu = ref(false)
let press: ReturnType<typeof setTimeout> | null = null
function cancelPress() { if (press) clearTimeout(press); press = null }
function openMenu() { cancelPress(); if (props.item.kind === 'server' && !redacted.value && ['text', 'image', 'voice', 'file'].includes(view.value.type)) menu.value = true }
function startPress(e: PointerEvent) { if (e.button !== 0) return; cancelPress(); press = setTimeout(openMenu, 550) }
function action(a: 'recall' | 'pin' | 'reply') { menu.value = false; if (props.item.kind === 'server') emit('action', a, props.item.message.id) }
async function copy() { await copyText(view.value.text ?? ''); menu.value = false }
const pinned = computed(() => !!(view.value.payload as {pinned?: boolean} | null)?.pinned)
const canRecall = computed(() => props.item.kind === 'server' && props.item.message.sender.id === props.selfId && nowTick.value - Date.parse(view.value.createdAt) <= 180000)
onBeforeUnmount(cancelPress)

const systemText = computed(() => {
  const p = view.value.payload as SystemPayload | null
  const event = p?.event
  if (event && te(`chat.system.${event}`)) {
    const params: Record<string, unknown> = { ...p }
    if (typeof p?.amount === 'string') {
      params.amount = formatMoney(p.amount, typeof p.currency === 'string' ? p.currency : 'CNY', locale.value)
    }
    if (event === 'delivered' && view.value.createdAt) {
      const at = typeof p?.delivered_at === 'string' ? p.delivered_at : view.value.createdAt
      const note = typeof p?.note === 'string' && p.note.trim() ? ` — ${p.note.trim()}` : ''
      return (
        t('chat.system.deliveredAt', {
          time: formatChatTime(at, locale.value),
          ago: formatRelative(at, locale.value, nowTick.value),
        }) + note
      )
    }
    const note = typeof p?.note === 'string' && p.note.trim() ? ` — ${p.note.trim()}` : ''
    if (event === 'completed' && p?.auto === true) return t('chat.system.completedAuto')
    if (event.startsWith('after_sales_') && note) return t(`chat.system.${event}`, params) + note
    if (typeof params.name !== 'string') params.name = typeof p?.display_name === 'string' ? p.display_name : ''
    return t(`chat.system.${event}`, params)
  }
  return view.value.text || t('chat.system.unknown')
})
</script>

<template>
  <div v-if="centered" class="gc-system" role="note">{{ systemText }}</div>
  <div v-else class="gc-row" :class="{ self, 'is-card': isCard }" @contextmenu.prevent="openMenu" @pointerdown="startPress" @pointerup="cancelPress" @pointercancel="cancelPress" @pointermove="cancelPress" @pointerleave="cancelPress">
    <button v-if="item.kind === 'server' && !redacted && !isCard" class="gc-action-toggle" type="button" :aria-label="t('chatActions.menu')" @click="openMenu">⋯</button>
    <div v-if="menu" class="gc-actions" role="group" :aria-label="t('chatActions.menu')" @keydown.esc="menu = false">
      <button v-if="view.text" type="button" @click="copy">{{ t('chatActions.copy') }}</button>
      <button v-if="view.text" type="button" @click="action('reply')">{{ t('chatActions.reply') }}</button>
      <button type="button" @click="action('pin')">{{ t(pinned ? 'chatActions.unpin' : 'chatActions.pin') }}</button>
      <button v-if="canRecall" type="button" @click="action('recall')">{{ t('chatActions.recall') }}</button>
      <button type="button" @click="menu = false">{{ t('common.cancel') }}</button>
    </div>
    <span class="gc-avatar" aria-hidden="true"><img v-if="avatarSrc" :src="avatarSrc" alt="" loading="lazy" decoding="async" />{{ avatarSrc ? '' : initial }}</span>
    <div class="gc-col">
      <span v-if="!self && mode === 'staff' && view.senderName" class="gc-name">{{ view.senderName }}</span>
      <span v-else-if="self && mode === 'staff' && view.senderName" class="gc-name gc-name-self">{{ view.senderName }}</span>
      <div class="gc-line">
        <span v-if="view.state === 'sending'" class="gc-state" :aria-label="t('chat.sending')"><span class="gc-spin" /></span>
        <span v-else-if="view.state === 'failed'" class="gc-state gc-failed">
          <button
            type="button"
            class="gc-retry"
            :aria-label="t('chat.retry')"
            :title="t('chat.failed')"
            @click="emit('retry', view.clientMsgId!)"
          >!</button>
          <button type="button" class="gc-discard" @click="emit('discard', view.clientMsgId!)">{{ t('chat.discard') }}</button>
        </span>

        <div v-if="redacted" class="gc-bubble gc-redacted" :class="{ self }">{{ t((view.payload as {recalled?: boolean} | null)?.recalled ? 'chatActions.recalled' : 'chat.redacted') }}</div>

        <div v-else-if="view.type === 'text'" class="gc-bubble" :class="{ self }">
          <SmartText :text="view.text" :view-order="viewOrder" @open-order-no="emit('openOrderNo', $event)" />
        </div>

        <button
          v-else-if="view.type === 'image'"
          type="button"
          class="gc-image"
          :style="imgBox"
          :aria-label="t('chat.imageAlt')"
          @click="view.imageUrl && emit('preview', view.imageUrl)"
        >
          <img v-if="view.imageUrl" :src="previewImageUrl(view.imageUrl) ?? undefined" :alt="t('chat.imageAlt')" loading="lazy" decoding="async" />
        </button>

        <VoiceBubble
          v-else-if="view.type === 'voice'"
          :src="view.voiceUrl"
          :duration-ms="view.durationMs"
          :self="self"
          :disabled="view.state === 'sending'"
        />

        <div v-else-if="view.type === 'file' && file" class="gc-bubble gc-file" :class="{ self }">
          <div class="gc-file-name">{{ file.name }} <span class="gc-file-size">{{ formatBytes(file.bytes, locale) }}</span></div>
          <div class="gc-file-preview">{{ file.preview }}</div>
          <div v-if="view.fileUrl" class="gc-file-actions">
            <button type="button" class="gc-btn" @click="emit('openFile', { url: view.fileUrl!, name: file.name })">{{ t('chat.file.open') }}</button>
            <a class="gc-btn" :href="view.fileUrl" :download="file.name" rel="noopener">{{ t('chat.file.download') }}</a>
          </div>
        </div>

        <ProductCardView
          v-else-if="view.type === 'product_card' && view.payload"
          :product="view.payload as ProductCardPayload"
          :actions="mode === 'buyer'"
          @open="emit('openProduct', $event)"
          @buy="emit('buyProduct', $event)"
        />
        <QuoteCardView
          v-else-if="view.type === 'quote_card' && view.payload"
          :quote="view.payload as QuoteCardPayload"
          :mode="mode"
          :busy="busyQuoteId === (view.payload as QuoteCardPayload).quote_id"
          @accept="emit('acceptQuote', $event)"
          @withdraw="emit('withdrawQuote', $event)"
          @open-order="emit('openOrder', $event)"
        />
        <OrderCardView
          v-else-if="view.type === 'order_card' && view.payload"
          :order="view.payload as OrderCardPayload"
          @open="emit('openOrder', $event)"
        />
        <div v-else class="gc-bubble">{{ view.text || t('chat.system.unknown') }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gc-row { position: relative }
.gc-action-toggle { align-self: center; border: 0; background: transparent; cursor: pointer; min-width: 44px; min-height: 44px; color: inherit }
.gc-actions { position: absolute; z-index: 5; top: 100%; inset-inline-start: 0; display: flex; flex-wrap: wrap; max-width: 100%; padding: 6px; background: var(--gc-panel, white); border: 1px solid #999; border-radius: 8px; box-shadow: 0 4px 12px #0002 }
.gc-actions button { min-height: 44px; padding: 8px; background: transparent; border: 0; cursor: pointer; color: inherit }
</style>
