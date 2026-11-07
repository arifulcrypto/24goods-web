<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  formatMoney,
  goBack,
  messageFilename,
  isApiError,
  safePayUrl,
  type Conversation,
  type Product,
  type QuoteCardPayload,
} from '@24goods/shared'
import { AgentStatusDot, ChatMessageList, useConversation } from '@24goods/shared/chat'
import { api } from '../api'
import BuySheet from '../components/BuySheet.vue'
import ChatComposer from '../components/ChatComposer.vue'
import ChatDelivery from '../components/ChatDelivery.vue'
import CredentialsSheet from '../components/CredentialsSheet.vue'
import PowStatus from '../components/PowStatus.vue'
import { channelId } from '../lib/orderActions'
import { useEnsureAccount } from '../composables/useEnsureAccount'
import { isPowAbort } from '../composables/usePow'
import { useAuthStore } from '../stores/auth'
import { useSiteStore } from '../stores/site'
import { stashAccessCode } from '../lib/attribution'
import TurnstileBox from '../components/TurnstileBox.vue'
import { useConversationsStore } from '../stores/conversations'
import { useRealtimeStore } from '../stores/realtime'
import { useSupportStore } from '../stores/support'
import { closeToast, confirm, showImagePreview, showLoadingToast, toastError } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const convs = useConversationsStore()
const rt = useRealtimeStore()
const support = useSupportStore()
const auth = useAuthStore()
const site = useSiteStore()
const account = useEnsureAccount()
const connecting = ref(false)
const connectFailed = ref(false)
const buyProduct = ref<Product | null>(null)
const buySheet = ref(false)
const showCreds = ref(false)

const conv = ref<Conversation | null>(null)
const busyQuoteId = ref<string | null>(null)
const list = ref<InstanceType<typeof ChatMessageList> | null>(null)
const opening = ref(true)

const chat = useConversation({
  selfRole: 'buyer',
  adapter: {
    list: (id, q) => api.conversations.messages(id, q),
    send: (id, b) => api.conversations.send(id, b),
    upload: (file, o) => api.upload(file, o),
    markRead: (id, mid) => api.conversations.read(id, mid),
  },
  onError: (e) => {
    if (!(isApiError(e) && e.code === 'unauthorized')) toastError(e)
  },
})

const status = computed(() => support.status?.status ?? 'offline')
const statusHint = computed(() => {
  const s = support.status
  if (s?.message) return s.message
  if (status.value === 'on_hold') return t('support.onHoldHint')
  if (status.value === 'offline') return t('support.offlineHint')
  return ''
})
const offline = computed(() => status.value === 'offline')
const offlineText = computed(() => {
  const h = site.site?.support_hours
  return h ? t('cs.offline', { hours: h }) : t('cs.offlineNoHours')
})
const welcome = computed(() => site.site?.support?.welcome?.trim() || t('cs.welcomeDefault'))
const quick = computed(() => (site.site?.support?.quick_questions ?? []).filter((q) => q.trim()).slice(0, 6))
const guestId = computed(() => (auth.user?.is_instant ? (auth.user.handle ?? '') : ''))
function saveLogin() {
  if (auth.guestCreds) showCreds.value = true
  else void router.push('/me')
}
function credsDone() {
  showCreds.value = false
}
async function onBuyProduct(id: string) {
  try {
    buyProduct.value = await api.product(id)
    buySheet.value = true
  } catch (e) {
    toastError(e)
  }
}
const closed = computed(() => conv.value?.status === 'closed')
const imageUrls = computed(() =>
  chat.messages.value.filter((m) => m.type === 'image' && m.attachment).map((m) => m.attachment!.url),
)

function markReadIfVisible() {
  if (document.visibilityState !== 'visible' || !chat.conversationId.value) return
  void chat.markRead()
  convs.markRead(chat.conversationId.value)
}

async function openConversation() {
  opening.value = true
  connectFailed.value = false
  try {
    // No account yet: show the UI only. The instant account is created when the visitor sends
    // something (bots/crawlers must not burn the instant-account quota just by loading /chat).
    if (!auth.loggedIn) return
    let id = typeof route.query.cid === 'string' ? route.query.cid : ''
    if (!id) {
      const productId = typeof route.query.product === 'string' && route.query.product ? route.query.product : undefined
      const c = await api.conversations.open(productId)
      conv.value = c
      id = c.id
      void router.replace({ path: '/chat', query: { cid: id } })
    } else {
      if (!convs.loaded) await convs.refresh().catch(() => undefined)
      conv.value = convs.list.find((c) => c.id === id) ?? null
    }
    convs.activeId = id
    await chat.open(id)
    await list.value?.scrollToBottom()
    markReadIfVisible()
  } catch (e) {
    connectFailed.value = true
    if (!isPowAbort(e)) toastError(e)
  } finally {
    opening.value = false
  }
}

/** Run `send` once a conversation exists, creating the instant account + conversation on first use. */
async function whenReady(send: () => void): Promise<void> {
  if (chat.conversationId.value) {
    send()
    return
  }
  if (connecting.value) return
  connecting.value = true
  connectFailed.value = false
  try {
    await account.ensure()
    await openConversation()
    if (chat.conversationId.value) send()
  } catch (e) {
    connectFailed.value = true
    if (!isPowAbort(e)) toastError(e)
  } finally {
    connecting.value = false
  }
}

async function afterIncoming(forceScroll: boolean) {
  const near = list.value?.isNearBottom() ?? true
  await nextTick()
  if (near || forceScroll) await list.value?.scrollToBottom(true)
  markReadIfVisible()
}

const offs = [
  rt.on('message', (e) => {
    if (e.conversation_id !== chat.conversationId.value) return
    chat.apply([e.message])
    void afterIncoming(e.message.sender.role === 'buyer')
  }),
  rt.on('conversation', (c) => {
    if (c.id === chat.conversationId.value) conv.value = c
  }),
  rt.on('sync', () => {
    void chat
      .catchUp()
      .then((n) => {
        if (n) void afterIncoming(false)
      })
      .catch(() => undefined)
  }),
]

function onVisibility() {
  if (document.visibilityState === 'visible') {
    void chat.catchUp().then(() => markReadIfVisible()).catch(() => undefined)
  }
}

onMounted(() => {
  void support.load()
  void site.load()
  void openConversation()
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => {
  offs.forEach((off) => off())
  document.removeEventListener('visibilitychange', onVisibility)
  convs.activeId = null
})
watch(
  () => route.query.cid,
  (v, old) => {
    if (v && old && v !== old) void openConversation()
  },
)

async function loadOlder() {
  await list.value?.preserveScroll(() => chat.loadOlder())
}

function sent() {
  void list.value?.scrollToBottom(true)
}
function onSendText(text: string) {
  void whenReady(() => {
    chat.sendText(text)
    sent()
  })
}
function onSendFile(text: string) {
  void whenReady(() => {
    chat.sendFile(text, messageFilename())
    sent()
  })
}
function onSendImage(blob: Blob, meta: { width?: number; height?: number; filename: string }) {
  void whenReady(() => {
    chat.sendImage(blob, meta)
    sent()
  })
}
function onSendVoice(blob: Blob, durationMs: number, filename: string) {
  void whenReady(() => {
    chat.sendVoice(blob, durationMs, filename)
    sent()
  })
}

function preview(url: string) {
  const all = imageUrls.value.includes(url) ? imageUrls.value : [url]
  showImagePreview({ images: all, startPosition: Math.max(0, all.indexOf(url)), closeable: true })
}

async function acceptQuote(q: QuoteCardPayload) {
  if (busyQuoteId.value) return
  const ok = await confirm(
    t('chat.acceptTitle'),
    t('chat.acceptMessage', { amount: formatMoney(q.amount, q.currency, locale.value), title: q.title }),
  )
  if (!ok) return
  busyQuoteId.value = q.quote_id
  let orderId: string | null = null
  try {
    const order = await api.quotes.accept(q.quote_id)
    orderId = order.id
    stashAccessCode(order.id, order.access_code)
    chat.patchQuote({ quote_id: q.quote_id, status: 'accepted', order_id: order.id })
    const { pay_url } = await api.orders.pay(order.id, await channelId())
    const safe = safePayUrl(pay_url)
    if (!safe) throw new Error('unsafe pay_url')
    showLoadingToast({ message: t('chat.redirecting'), forbidClick: true, duration: 0 })
    window.location.assign(safe)
  } catch (e) {
    closeToast()
    if (isApiError(e) && e.code === 'quote_expired') chat.patchQuote({ quote_id: q.quote_id, status: 'expired' })
    toastError(e)
    if (orderId) void router.push(`/orders/${encodeURIComponent(orderId)}`)
  } finally {
    busyQuoteId.value = null
  }
}

async function startNew() {
  try {
    const c = await api.conversations.open()
    conv.value = c
    await router.replace({ path: '/chat', query: { cid: c.id } })
  } catch (e) {
    toastError(e)
  }
}

function back() {
  goBack(router, '/messages')
}
function chip(kind: 'presales' | 'order' | 'delivery') {
  if (kind === 'order') void router.push('/orders')
  else onSendText(t(kind === 'presales' ? 'chat.chips.presalesMsg' : 'chat.chips.deliveryMsg'))
}
function deliveredOrderId(it: { kind: string; message?: { type: string; payload: unknown } }): string | null {
  if (it.kind !== 'server' || it.message?.type !== 'system') return null
  const p = it.message.payload as { event?: string; order_id?: string } | null
  return p?.event === 'delivered' && typeof p.order_id === 'string' ? p.order_id : null
}
</script>

<template>
  <div class="chat-page">
    <header class="chat-head">
      <button type="button" class="head-btn" :aria-label="t('common.back')" @click="back">
        <van-icon name="arrow-left" size="20" />
      </button>
      <div class="head-main">
        <div class="head-title">{{ conv?.assigned_agent?.display_name || t('chat.title') }}</div>
        <div class="head-status">
          <AgentStatusDot :status="status" />
          <span>{{ t(`agentStatus.${status}`) }}</span>
          <span v-if="statusHint" class="head-msg">· {{ statusHint }}</span>
        </div>
      </div>
      <span class="head-btn" aria-hidden="true" />
    </header>

    <div v-if="guestId" class="notice guest">
      <span>{{ t('cs.guestBanner', { id: guestId }) }}</span>
      <button type="button" class="notice-btn" @click="saveLogin">{{ t('cs.saveLogin') }}</button>
    </div>
    <div v-if="offline" class="notice offline" role="status">{{ offlineText }}</div>

    <ChatMessageList
      ref="list"
      :items="chat.items.value"
      mode="buyer"
      :loading="(opening && !connecting) || chat.loading.value"
      :loading-older="chat.loadingOlder.value"
      :has-more="chat.hasMore.value"
      :busy-quote-id="busyQuoteId"
      @reach-top="loadOlder"
      @preview="preview"
      view-order
      @open-product="(id) => router.push(`/p/${encodeURIComponent(id)}`)"
      @buy-product="onBuyProduct"
      @open-order-no="(no) => router.push(`/orders/${encodeURIComponent(no)}`)"
      @open-order="(id) => router.push(`/orders/${encodeURIComponent(id)}`)"
      @accept-quote="acceptQuote"
      @retry="chat.retry"
      @discard="chat.discard"
    >
      <template #empty>
        <div v-if="connecting" class="connecting" role="status">
          <van-loading size="20" /> <span>{{ t('cs.connecting') }}</span>
          <PowStatus :solving="account.solving.value" :progress="account.progress.value" />
        </div>
        <button v-else-if="connectFailed" type="button" class="connecting retry" @click="connectFailed = false; void openConversation()">
          {{ t('cs.connectFailed') }}
        </button>
        <div v-else class="gc-row welcome">
          <span class="gc-avatar" aria-hidden="true">{{ (conv?.assigned_agent?.display_name || t('chat.title')).charAt(0).toUpperCase() }}</span>
          <div class="gc-col">
            <div class="gc-line"><div class="gc-bubble">{{ welcome }}</div></div>
            <div v-if="!quick.length" class="chips" role="group" :aria-label="t('chat.chips.label')">
              <button type="button" class="chip" @click="chip('presales')">{{ t('chat.chips.presales') }}</button>
              <button type="button" class="chip" @click="chip('order')">{{ t('chat.chips.order') }}</button>
              <button type="button" class="chip" @click="chip('delivery')">{{ t('chat.chips.delivery') }}</button>
            </div>
          </div>
        </div>
      </template>
      <template #item-extra="{ item }">
        <ChatDelivery v-if="deliveredOrderId(item)" :order-id="deliveredOrderId(item)!" />
      </template>
    </ChatMessageList>

    <div v-if="!closed && quick.length" class="quick" role="group" :aria-label="t('chat.chips.label')">
      <button v-for="q in quick" :key="q" type="button" class="chip" @click="onSendText(q)">{{ q }}</button>
    </div>
    <div v-if="!closed && !auth.loggedIn && account.tsNeeded.value" class="ts-gate">
      <p class="muted small">{{ t('cs.verify') }}</p>
      <TurnstileBox :ts="account.ts" />
    </div>
    <div v-if="closed" class="closed-bar">
      <span>{{ t('chat.closed') }}</span>
      <van-button size="small" round type="primary" @click="startNew">{{ t('chat.startNew') }}</van-button>
    </div>
    <ChatComposer
      v-else
      :voice="site.features.voice_messages"
      :disabled="connecting"
      @send-text="onSendText"
      @send-file="onSendFile"
      @send-image="onSendImage"
      @send-voice="onSendVoice"
      @focus="list?.scrollToBottom()"
    />
    <BuySheet v-model:show="buySheet" :product="buyProduct" />
    <CredentialsSheet :show="showCreds" :credentials="auth.guestCreds" @done="credsDone" />
  </div>
</template>

<style scoped>
.ts-gate {
  padding: 4px 12px;
}
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  background: var(--c-bg);
}
.chat-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 52px;
  padding: var(--safe-top) 4px 0;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-line);
}
.head-btn {
  width: 44px;
  height: 44px;
  flex: none;
  border: 0;
  background: none;
  display: grid;
  place-items: center;
  color: var(--c-text);
  cursor: pointer;
}
.head-main {
  flex: 1;
  min-width: 0;
  text-align: center;
}
.head-title {
  font-weight: 700;
  font-size: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.head-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  color: var(--c-text-2);
  min-width: 0;
}
.head-msg {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--c-muted);
}
.closed-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px calc(10px + var(--safe-bottom));
  background: var(--c-surface);
  border-top: 1px solid var(--c-line);
  color: var(--c-text-2);
  font-size: 13px;
}
.connecting {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 16px;
  color: var(--c-text-2);
  font-size: 13px;
}
.connecting.retry {
  width: 100%;
  min-height: 44px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  cursor: pointer;
}
.notice {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 12px;
  min-height: 36px;
  font-size: 12px;
}
.notice.guest {
  background: var(--c-accent-soft);
  color: var(--c-accent-ink);
}
.notice.offline {
  background: #fff4e5;
  color: #8a4b00;
  padding: 8px 12px;
}
.notice-btn {
  flex: none;
  min-height: 44px;
  padding: 0 4px;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 700;
  text-decoration: underline;
  cursor: pointer;
}
.quick {
  flex: none;
  display: flex;
  gap: 8px;
  padding: 6px 12px;
  overflow-x: auto;
  background: var(--c-surface);
  border-top: 1px solid var(--c-line);
}
.quick .chip {
  flex: none;
  max-width: 80vw;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.welcome {
  margin-top: 8px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 8px;
}
.chip {
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--c-line);
  border-radius: 999px;
  background: var(--c-surface);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
</style>
