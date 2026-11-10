<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { previewImageUrl } from '../../media/preferences'
import { copyText } from '../../util'
import { nowTick } from './tick'
import { useI18n } from 'vue-i18n'
import type { FilePayload, MessageMeta, OrderCardPayload, ProductCardPayload, QuoteCardPayload, ReplyRef, SystemPayload } from '../../types'
import { formatBytes, formatChatTime, formatMoney, formatRelative } from '../../format'
import { safeAssetUrl } from '../../api/endpoints'
import { resolveAvatar } from '../../avatars'
import type { ChatItem } from '../useConversation'
import VoiceBubble from './VoiceBubble.vue'
import QuoteCardView from './QuoteCardView.vue'
import OrderCardView from './OrderCardView.vue'
import ProductCardView from './ProductCardView.vue'
import SmartText from './SmartText.vue'
import MessageMenu, { type MenuItem } from './MessageMenu.vue'

const props = defineProps<{ item: ChatItem; mode: 'buyer' | 'staff'; busyQuoteId?: string | null; viewOrder?: boolean; selfId?: string; selfName?: string; selfAvatar?: string | null; selfPreset?: string | null }>()
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
  action: [action: 'recall' | 'pin' | 'reply' | 'edit', id: number]
  jump: [id: number]
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
      senderPreset: props.selfPreset ?? null,
      senderId: props.selfId ?? null,
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
    senderPreset: m.sender.avatar_preset ?? null,
    senderId: m.sender.id,
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

const avatarSrc = computed(() =>
  resolveAvatar({ url: safeAssetUrl(view.value.senderAvatar), preset: view.value.senderPreset, seed: view.value.senderId ?? view.value.senderRole }),
)

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
const menuAt = ref({ x: 0, y: 0 })
let press: ReturnType<typeof setTimeout> | null = null
function cancelPress() { if (press) clearTimeout(press); press = null }
const menuable = computed(() => props.item.kind === 'server' && !redacted.value && ['text', 'image', 'voice', 'file'].includes(view.value.type))
function openMenu(e?: MouseEvent | PointerEvent) {
  cancelPress()
  if (!menuable.value) return
  if (e && 'clientX' in e) menuAt.value = { x: e.clientX, y: e.clientY }
  menu.value = true
}
let pressFrom: { x: number; y: number } | null = null
function startPress(e: PointerEvent) {
  if (e.button !== 0) return
  cancelPress()
  if ((e.target as Element | null)?.closest?.('a, button')) return
  pressFrom = { x: e.clientX, y: e.clientY }
  const at = { clientX: e.clientX, clientY: e.clientY } as PointerEvent
  press = setTimeout(() => openMenu(at), 550)
}
function movePress(e: PointerEvent) {
  if (!press || !pressFrom) return
  if (Math.hypot(e.clientX - pressFrom.x, e.clientY - pressFrom.y) > 8) cancelPress()
}
function onContextMenu(e: MouseEvent) {
  // Keep the native menu for links and for a text selection.
  if ((e.target as Element | null)?.closest?.('a')) return
  if ((window.getSelection?.()?.toString() ?? '') !== '') return
  e.preventDefault()
  openMenu(e)
}
const meta = computed(() => (view.value.payload as MessageMeta | null) ?? null)
const pinned = computed(() => !!meta.value?.pinned)
const reply = computed<ReplyRef | null>(() => meta.value?.reply_to ?? null)
const edited = computed(() => !!meta.value?.edited_at)
const own = computed(() => props.item.kind === 'server' && props.item.message.sender.id === props.selfId)
const age = computed(() => nowTick.value - Date.parse(view.value.createdAt))
const canRecall = computed(() => own.value && age.value <= 180000)
const canEdit = computed(() => own.value && view.value.type === 'text' && age.value <= 48 * 3600 * 1000)
const items = computed<MenuItem[]>(() => {
  const l: MenuItem[] = [{ key: 'reply', icon: '↩', label: t('chatActions.reply') }]
  if (view.value.type === 'text' && view.value.text) l.push({ key: 'copy', icon: '⧉', label: t('chatActions.copy') })
  l.push({ key: 'pin', icon: '📌', label: t(pinned.value ? 'chatActions.unpin' : 'chatActions.pin') })
  if (canEdit.value) l.push({ key: 'edit', icon: '✎', label: t('chatActions.edit') })
  if (canRecall.value) l.push({ key: 'recall', icon: '🗑', label: t('chatActions.recall'), danger: true })
  return l
})
async function pick(key: string) {
  menu.value = false
  if (key === 'copy') { await copyText(view.value.text ?? ''); return }
  if (props.item.kind === 'server') emit('action', key as 'recall' | 'pin' | 'reply' | 'edit', props.item.message.id)
}
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
  <div v-if="centered" class="gc-system" role="note" :data-mid="item.kind === 'server' ? item.message.id : undefined">{{ systemText }}</div>
  <div v-else class="gc-row" :class="{ self, 'is-card': isCard }" :data-mid="item.kind === 'server' ? item.message.id : undefined" @contextmenu="onContextMenu" @pointerdown="startPress" @pointerup="cancelPress" @pointercancel="cancelPress" @pointermove="movePress" @pointerleave="cancelPress">
    <button v-if="menuable && !isCard" class="gc-action-toggle" type="button" :aria-label="t('chatActions.menu')" aria-haspopup="menu" @click="openMenu">⋯</button>
    <MessageMenu :open="menu" :items="items" :x="menuAt.x" :y="menuAt.y" :label="t('chatActions.menu')" @select="pick" @close="menu = false" />
    <span class="gc-avatar" aria-hidden="true"><img :src="avatarSrc" alt="" loading="lazy" decoding="async" /></span>
    <div class="gc-col">
      <span v-if="!self && mode === 'staff' && view.senderName" class="gc-name">{{ view.senderName }}</span>
      <span v-else-if="self && mode === 'staff' && view.senderName" class="gc-name gc-name-self">{{ view.senderName }}</span>
      <button v-if="reply && !redacted" type="button" class="gc-reply" :class="{ self }" @click="emit('jump', reply.id)">
        <span class="gc-reply-name">{{ reply.sender_name || t('chatActions.unknownSender') }}</span>
        <span class="gc-reply-text">{{ reply.preview || t('chatActions.recalled') }}</span>
      </button>
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
      <span v-if="edited && !redacted" class="gc-edited">{{ t('chatActions.edited') }}</span>
    </div>
  </div>
</template>

<style scoped>
.gc-row { position: relative }
.gc-action-toggle { align-self: center; border: 0; background: transparent; cursor: pointer; min-width: 44px; min-height: 44px; color: inherit }
.gc-reply { display: flex; flex-direction: column; align-items: flex-start; max-width: 100%; min-height: 44px; padding: 4px 10px; margin: 0 0 2px; border: 0; border-inline-start: 3px solid var(--gc-accent, #3b82f6); border-radius: 6px; background: rgb(127 127 127 / 0.14); color: inherit; font: inherit; font-size: 12px; text-align: start; cursor: pointer; box-sizing: border-box }
.gc-reply-name { font-weight: 700; color: var(--gc-accent, #3b82f6); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.gc-reply-text { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 0.85 }
@media (pointer: coarse) { .gc-bubble { -webkit-touch-callout: none; user-select: none } }
.gc-edited { font-size: 11px; opacity: 0.6; margin: 0 4px }
:global(.gc-jump-flash) { animation: gc-flash 1.2s ease-out }
@media (prefers-reduced-motion: reduce) { :global(.gc-jump-flash) { animation: none } }
@keyframes gc-flash { 0% { background: rgb(59 130 246 / 0.28) } 100% { background: transparent } }
</style>
