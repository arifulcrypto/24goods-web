<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, InfoFilled } from '@element-plus/icons-vue'
import {
  formatRelative,
  messagePreview,
  userAvatar,
  type Conversation,
  type ConversationFilter,
  type Message,
  type MessageMeta,
  type QuoteCardPayload,
} from '@24goods/shared'
import { useTyping } from '@24goods/shared/chat'
import { ChatMessageList, useConversation } from '@24goods/shared/chat'
import { api, apiV4 } from '../api'
import InboxSidePanel from '../components/InboxSidePanel.vue'
import OrderDrawer from '../components/OrderDrawer.vue'
import StaffComposer from '../components/StaffComposer.vue'
import { useAuthStore } from '../stores/auth'
import { useRealtimeStore } from '../stores/realtime'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const rt = useRealtimeStore()

const filter = ref<ConversationFilter>('all')
const cstatus = ref<'open' | 'closed'>('open')
const list = ref<Conversation[]>([])
const listLoading = ref(false)
const page = ref(1)
const total = ref(0)
const active = ref<Conversation | null>(null)
const busy = ref<'assign' | 'close' | null>(null)
const detailsOpen = ref(false)
const orderOpen = ref<string | null>(null)
const msgList = ref<InstanceType<typeof ChatMessageList> | null>(null)
const width = ref(window.innerWidth)
const PAGE = 30

const activeId = computed(() => (typeof route.query.c === 'string' ? route.query.c : null))
const me = computed(() => auth.user?.id ?? '')
const wideSide = computed(() => width.value >= 1280)
const narrow = computed(() => width.value < 820)

const replyTo = ref<Message | null>(null)
const editing = ref<Message | null>(null)
const pins = ref<Message[]>([])
const replyBar = computed(() =>
  replyTo.value
    ? {
        title: t('chatActions.replyingTo', { name: replyTo.value.sender.display_name }),
        preview: replyTo.value.type === 'text' ? (replyTo.value.text ?? '').slice(0, 120) : t(`chat.preview.${replyTo.value.type}`),
      }
    : null,
)
async function refreshPins() {
  const id = chat.conversationId.value
  if (!id) return
  try {
    pins.value = await api.conversations.pins(id)
  } catch {
    /* convenience only */
  }
}
async function messageAction(action: 'recall' | 'pin' | 'reply' | 'edit', id: number) {
  const m = chat.messages.value.find((m) => m.id === id)
  if (!m) return
  if (action === 'reply') {
    editing.value = null
    replyTo.value = m
    return
  }
  if (action === 'edit') {
    replyTo.value = null
    editing.value = m
    return
  }
  try {
    const updated = action === 'recall' ? await api.chatActions.recall(id) : await api.chatActions.pin(id, !(m.payload as MessageMeta | null)?.pinned)
    chat.apply([updated])
    void refreshPins()
  } catch (e) {
    toastError(e)
  }
}
async function saveEdit(text: string) {
  const m = editing.value
  if (!m) return
  try {
    chat.apply([await api.chatActions.edit(m.id, text)])
    if (editing.value === m) editing.value = null
  } catch (e) {
    toastError(e)
  }
}
const typing = useTyping({
  send: () => api.conversations.typing(chat.conversationId.value!),
  conversationId: () => chat.conversationId.value,
  otherRole: 'buyer',
})
/** The reply target is consumed by the next send. */
function takeReply(): number | undefined {
  const id = replyTo.value?.id
  replyTo.value = null
  return id
}
const chat = useConversation({
  selfRole: 'agent',
  adapter: {
    list: (id, q) => api.conversations.messages(id, q),
    send: (id, b) => api.conversations.send(id, b),
    upload: (file, o) => api.upload(file, o),
    // no staff read-receipt endpoint in the contract; unread is reset locally
  },
  onError: (e) => toastError(e),
})

// ---------------- after-sales link bar ----------------
// The shared bubble renders `file` messages and the after-sales system cards (server-sent
// payload `{event, order_id, after_sales_id, note?}`); the staff only get a deep link here.
type SystemPayload = { event?: string; after_sales_id?: string }
const payloadOf = (m: Message) => (m.payload ?? {}) as SystemPayload
const isAfterSales = (m: Message) => m.type === 'system' && (payloadOf(m).event ?? '').startsWith('after_sales')
const afterSalesCard = computed(() => chat.messages.value.filter(isAfterSales).at(-1) ?? null)

// ---------------- conversation list ----------------
function matches(c: Conversation): boolean {
  if (c.status !== cstatus.value) return false
  if (filter.value === 'mine') return c.assigned_agent?.id === me.value
  if (filter.value === 'unassigned') return !c.assigned_agent
  return true
}
const sortList = (l: Conversation[]) => [...l].sort((a, b) => b.updated_at.localeCompare(a.updated_at))

async function loadList(p = 1) {
  listLoading.value = true
  try {
    const r = await api.conversations.list({ filter: filter.value, status: cstatus.value, page: p, page_size: PAGE })
    list.value = p === 1 ? r.items : sortList([...list.value, ...r.items.filter((x) => !list.value.some((y) => y.id === x.id))])
    page.value = r.page || p
    total.value = r.total
    if (activeId.value && !active.value) active.value = list.value.find((c) => c.id === activeId.value) ?? null
  } catch (e) {
    toastError(e)
  } finally {
    listLoading.value = false
  }
}
watch([filter, cstatus], () => void loadList(1))

function upsert(c: Conversation) {
  const rest = list.value.filter((x) => x.id !== c.id)
  list.value = matches(c) ? sortList([c, ...rest]) : rest
  if (active.value?.id === c.id) active.value = c
}
let refreshTimer: ReturnType<typeof setTimeout> | null = null
function refreshSoon() {
  if (refreshTimer) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    void loadList(1)
  }, 800)
}

// ---------------- active conversation ----------------
async function select(c: Conversation) {
  if (c.id === activeId.value) return
  await router.replace({ query: { ...route.query, c: c.id } })
}
watch(
  activeId,
  async (id) => {
    replyTo.value = null
    editing.value = null
    pins.value = []
    typing.clear()
    if (!id) {
      active.value = null
      chat.reset()
      return
    }
    active.value = list.value.find((c) => c.id === id) ?? active.value
    if (active.value && active.value.id === id && active.value.unread) upsert({ ...active.value, unread: 0 })
    await chat.open(id)
    void refreshPins()
    await msgList.value?.scrollToBottom()
  },
  { immediate: true },
)

async function afterIncoming(force: boolean) {
  const near = msgList.value?.isNearBottom() ?? true
  await nextTick()
  if (near || force) await msgList.value?.scrollToBottom(true)
}

// ---------------- realtime ----------------
// Buyer messages and new order cards (every order is visible to CS) raise the unread badge.
const countsAsUnread = (m: { sender: { role: string }; type: string }) => m.sender.role === 'buyer' || m.type === 'order_card'
const offs = [
  rt.on('conversation', (c) => upsert(c)),
  rt.on('typing', (e) => typing.onEvent(e)),
  rt.on('message', (e) => {
    const isActive = e.conversation_id === activeId.value
    if (isActive) {
      chat.apply([e.message])
      typing.clearFor(e.message.sender.role, e.conversation_id)
      const p = (e.message.payload ?? {}) as MessageMeta & { event?: string }
      if (p.pinned !== undefined || p.event === 'message_pinned' || p.event === 'message_unpinned' || p.recalled || pins.value.some((x) => x.id === e.message.id)) void refreshPins()
      void afterIncoming(e.message.sender.role === 'agent' && e.message.sender.id === me.value)
    }
    const c = list.value.find((x) => x.id === e.conversation_id)
    if (c) {
      upsert({
        ...c,
        last_message: e.message,
        updated_at: e.message.created_at,
        unread: isActive || !countsAsUnread(e.message) ? c.unread : c.unread + 1,
      })
    } else if (countsAsUnread(e.message)) {
      refreshSoon()
    }
  }),
  rt.on('sync', () => {
    refreshSoon()
    void chat
      .catchUp()
      .then((n) => {
        if (n) void afterIncoming(false)
      })
      .catch(() => undefined)
  }),
]
function onResize() {
  width.value = window.innerWidth
}
onMounted(() => {
  void loadList(1)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  offs.forEach((o) => o())
  window.removeEventListener('resize', onResize)
  if (refreshTimer) clearTimeout(refreshTimer)
})

// ---------------- actions ----------------
async function assign(agentId: string | null) {
  const c = active.value
  if (!c || busy.value) return
  busy.value = 'assign'
  try {
    upsert(await api.conversations.assign(c.id, agentId))
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = null
  }
}
async function closeConv() {
  const c = active.value
  if (!c || busy.value) return
  if (!(await confirmBox(t('inbox.closeConfirm')))) return
  busy.value = 'close'
  try {
    upsert(await api.conversations.close(c.id))
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = null
  }
}
function sent() {
  void msgList.value?.scrollToBottom(true)
}
async function sendProduct(productId: string) {
  const id = chat.conversationId.value
  if (!id) return
  try {
    await apiV4.conversations.sendProductCard(id, productId)
    toastOk(t('inbox.pickSent'))
    await chat.catchUp()
    sent()
  } catch (e) {
    toastError(e)
  }
}
function onQuoteSent() {
  void chat.catchUp().then(() => sent())
}
const withdrawing = ref<string | null>(null)
async function withdrawQuote(q: QuoteCardPayload) {
  if (withdrawing.value || !(await confirmBox(t('inbox.withdrawConfirm')))) return
  withdrawing.value = q.quote_id
  try {
    chat.patchQuote(await api.quotes.withdraw(q.quote_id))
    toastOk(t('inbox.withdrawn'))
  } catch (e) {
    toastError(e)
  } finally {
    withdrawing.value = null
  }
}

// image preview via Element Plus viewer
const previewUrls = ref<string[]>([])
const previewIndex = ref(0)
function preview(url: string) {
  const all = chat.messages.value.filter((m) => m.type === 'image' && m.attachment).map((m) => m.attachment!.url)
  previewUrls.value = all.includes(url) ? all : [url]
  previewIndex.value = Math.max(0, previewUrls.value.indexOf(url))
}
function openProduct(id: string) {
  window.open(`/p/${encodeURIComponent(id)}`, '_blank', 'noopener')
}
</script>

<template>
  <div class="inbox" :class="{ narrow, 'has-active': !!activeId }">
    <!-- left: conversation list -->
    <aside class="pane list-pane">
      <div class="list-head">
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="all">{{ t('inbox.filter.all') }}</el-radio-button>
          <el-radio-button value="mine">{{ t('inbox.filter.mine') }}</el-radio-button>
          <el-radio-button value="unassigned">{{ t('inbox.filter.unassigned') }}</el-radio-button>
        </el-radio-group>
        <el-select v-model="cstatus" size="small" style="width: 100px">
          <el-option value="open" :label="t('inbox.open')" />
          <el-option value="closed" :label="t('inbox.closed')" />
        </el-select>
      </div>
      <div v-loading="listLoading && !list.length" class="list-body">
        <el-empty v-if="!list.length && !listLoading" :description="t('inbox.empty')" :image-size="80" />
        <button
          v-for="c in list"
          :key="c.id"
          type="button"
          class="conv"
          :class="{ on: c.id === activeId }"
          @click="select(c)"
        >
          <el-badge :value="c.unread" :hidden="!c.unread || c.id === activeId" :max="99">
            <el-avatar :size="38" :src="userAvatar(c.buyer)" />
          </el-badge>
          <span class="conv-main">
            <span class="conv-top">
              <span class="conv-name">{{ c.buyer.display_name }}</span>
              <span class="conv-time muted">{{ formatRelative(c.updated_at, locale) }}</span>
            </span>
            <span class="conv-preview muted">{{ messagePreview(c.last_message, t) || '—' }}</span>
            <span class="conv-meta">
              <el-tag v-if="c.assigned_agent" size="small" :type="c.assigned_agent.id === me ? 'primary' : 'info'" effect="plain">
                {{ c.assigned_agent.id === me ? t('inbox.you') : c.assigned_agent.display_name }}
              </el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">{{ t('inbox.unassigned') }}</el-tag>
              <span v-if="c.product" class="conv-product muted">{{ c.product.title }}</span>
            </span>
          </span>
        </button>
        <div v-if="list.length < total" class="more">
          <el-button text :loading="listLoading" @click="loadList(page + 1)">{{ t('inbox.loadMore') }}</el-button>
        </div>
      </div>
    </aside>

    <!-- center: chat -->
    <section class="pane chat-pane">
      <template v-if="active || activeId">
        <header class="chat-head">
          <el-button v-if="narrow" text :icon="ArrowLeft" :aria-label="t('common.back')" @click="router.replace({ query: {} })" />
          <div class="chat-title">
            <div class="name">{{ active?.buyer.display_name ?? '…' }}</div>
            <div class="muted small">
              <span class="typing" role="status">{{ typing.name.value === null ? '' : typing.name.value ? t('chatActions.typing', { name: typing.name.value }) : t('chatActions.typingGeneric') }}</span>
              <template v-if="typing.name.value === null">{{ active?.assigned_agent ? t('inbox.assignedTo', { name: active.assigned_agent.display_name }) : t('inbox.unassigned') }}</template>
            </div>
          </div>
          <div class="chat-actions">
            <el-button
              v-if="active && active.assigned_agent?.id !== me"
              size="small"
              type="primary"
              plain
              :loading="busy === 'assign'"
              @click="assign(me)"
            >
              {{ t('inbox.assignToMe') }}
            </el-button>
            <el-button v-if="active?.assigned_agent" size="small" :loading="busy === 'assign'" @click="assign(null)">
              {{ t('inbox.unassign') }}
            </el-button>
            <el-button v-if="active?.status === 'open'" size="small" type="danger" plain :loading="busy === 'close'" @click="closeConv">
              {{ t('inbox.close') }}
            </el-button>
            <el-button v-if="!wideSide" size="small" :icon="InfoFilled" @click="detailsOpen = true" />
          </div>
        </header>
        <div v-if="afterSalesCard" class="v3-bar">
          <RouterLink v-if="afterSalesCard" :to="{ path: '/after-sales', query: { open: payloadOf(afterSalesCard).after_sales_id } }">{{ t('inbox.openAfterSales') }}</RouterLink>
        </div>
        <ChatMessageList
      :self-id="auth.user?.id"
      :self-name="auth.user?.display_name"
      :self-avatar="auth.user?.avatar_url"
      :self-preset="auth.user?.avatar_preset"
      @action="messageAction"
          :pins="pins"
          :older-loader="() => chat.loadOlder()"
          ref="msgList"
          :items="chat.items.value"
          mode="staff"
          :loading="chat.loading.value"
          :loading-older="chat.loadingOlder.value"
          :has-more="chat.hasMore.value"
          @reach-top="msgList?.preserveScroll(() => chat.loadOlder())"
          @preview="preview"
          @open-product="openProduct"
          @open-order="(id) => (orderOpen = id)"
          @withdraw-quote="withdrawQuote"
          @retry="chat.retry"
          @discard="chat.discard"
        />
        <div v-if="active?.status === 'closed'" class="closed muted">{{ t('inbox.closedNotice') }}</div>
        <StaffComposer
          v-else
          :disabled="!chat.conversationId.value"
          :reply="replyBar"
          :editing="editing ? { text: editing.text ?? '' } : null"
          @send-text="(v) => (chat.sendText(v, takeReply()), sent())"
          @send-image="(b, m) => (chat.sendImage(b, m, takeReply()), sent())"
          @send-voice="(b, d, f) => (chat.sendVoice(b, d, f, takeReply()), sent())"
          @send-product="sendProduct"
          @cancel-reply="replyTo = null"
          @cancel-edit="editing = null"
          @save-edit="saveEdit"
          @typing="typing.ping()"
        />
      </template>
      <el-empty v-else :description="t('inbox.select')" class="placeholder" />
    </section>

    <!-- right: context -->
    <aside v-if="wideSide && active" class="pane side-pane">
      <InboxSidePanel
        :conversation="active"
        :messages="chat.messages.value"
        @quote-sent="onQuoteSent"
        :withdrawing="withdrawing"
        @withdraw="withdrawQuote"
        @open-order="(id) => (orderOpen = id)"
      />
    </aside>
    <el-drawer v-if="!wideSide" v-model="detailsOpen" size="min(360px, 100vw)" :with-header="false">
      <InboxSidePanel
        v-if="active"
        :conversation="active"
        :messages="chat.messages.value"
        @quote-sent="onQuoteSent"
        :withdrawing="withdrawing"
        @withdraw="withdrawQuote"
        @open-order="(id) => (orderOpen = id)"
      />
    </el-drawer>

    <OrderDrawer :order-id="orderOpen" @close="orderOpen = null" />
    <el-image-viewer
      v-if="previewUrls.length"
      :url-list="previewUrls"
      :initial-index="previewIndex"
      teleported
      @close="previewUrls = []"
    />
  </div>
</template>

<style scoped>
.inbox {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr) 330px;
  height: calc(100vh - 56px);
  min-height: 0;
}
@media (max-width: 1279px) {
  .inbox {
    grid-template-columns: 280px minmax(0, 1fr);
  }
}
.inbox.narrow {
  grid-template-columns: minmax(0, 1fr);
}
.inbox.narrow.has-active .list-pane,
.inbox.narrow:not(.has-active) .chat-pane {
  display: none;
}
.pane {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.list-pane {
  background: var(--a-surface);
  border-right: 1px solid var(--a-line);
}
.list-head {
  display: flex;
  gap: 8px;
  justify-content: space-between;
  align-items: center;
  padding: 10px;
  border-bottom: 1px solid var(--a-line);
}
.list-body {
  flex: 1;
  overflow-y: auto;
}
.conv {
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid #f1f2f4;
  background: none;
  text-align: start;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.conv:hover {
  background: #f8fafc;
}
.conv.on {
  background: #eff6ff;
}
.conv-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.conv-top {
  display: flex;
  justify-content: space-between;
  gap: 6px;
}
.conv-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-time {
  font-size: 11px;
  white-space: nowrap;
}
.conv-preview {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}
.conv-product {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.more {
  text-align: center;
  padding: 8px;
}
.chat-pane {
  background: #f3f4f6;
}
.chat-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--a-surface);
  border-bottom: 1px solid var(--a-line);
  min-height: 56px;
}
.chat-title {
  flex: 1;
  min-width: 0;
}
.chat-title .name {
  font-weight: 600;
}
.chat-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.chat-actions .el-button + .el-button {
  margin-left: 0;
}
.small {
  font-size: 12px;
}
.typing {
  color: var(--el-color-primary);
}
.closed {
  padding: 14px;
  text-align: center;
  background: var(--a-surface);
  border-top: 1px solid var(--a-line);
}
.placeholder {
  margin: auto;
}
.side-pane {
  border-left: 1px solid var(--a-line);
  overflow-y: auto;
  background: var(--a-bg);
}
.v3-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  padding: 6px 14px;
  font-size: 13px;
  border-bottom: 1px solid var(--a-line);
  background: var(--a-surface);
}
</style>
