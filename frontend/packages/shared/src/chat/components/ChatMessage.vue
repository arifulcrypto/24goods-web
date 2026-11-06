<script setup lang="ts">
import { computed } from 'vue'
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

const props = defineProps<{ item: ChatItem; mode: 'buyer' | 'staff'; busyQuoteId?: string | null; viewOrder?: boolean }>()
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
      senderName: '',
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
  <div v-else class="gc-row" :class="{ self, 'is-card': isCard }">
    <span v-if="!self" class="gc-avatar" aria-hidden="true">{{ initial }}</span>
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

        <div v-if="redacted" class="gc-bubble gc-redacted" :class="{ self }">{{ t('chat.redacted') }}</div>

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
          <img v-if="view.imageUrl" :src="view.imageUrl" :alt="t('chat.imageAlt')" loading="lazy" decoding="async" />
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
