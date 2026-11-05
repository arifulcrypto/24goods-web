<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  copyText,
  formatCountdown,
  formatDateTime,
  formatMoney,
  isApiError,
  safePayUrl,
  type Order,
} from '@24goods/shared'
import { api } from '../api'
import { useRealtimeStore } from '../stores/realtime'
import { closeToast, confirm, showImagePreview, showLoadingToast, showSuccessToast, showToast, toastError } from '../ui'

const { t, te, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const rt = useRealtimeStore()

const order = ref<Order | null>(null)
const loading = ref(true)
const notFound = ref(false)
const paying = ref(false)
const revealing = ref(false)
const confirming = ref(false)
/** Revealed codes live only in memory for this view; never persisted. */
const secrets = ref<string[]>([])
const now = ref(Date.now())
const timer = setInterval(() => (now.value = Date.now()), 1000)

const id = computed(() => String(route.params.id ?? ''))

async function fetchOrder(silent = false) {
  if (!id.value) return
  if (!silent) loading.value = true
  try {
    order.value = await api.orders.get(id.value)
    notFound.value = false
  } catch (e) {
    if (isApiError(e) && (e.code === 'not_found' || e.code === 'forbidden')) notFound.value = true
    else if (!silent) toastError(e)
  } finally {
    loading.value = false
  }
}
watch(
  id,
  () => {
    secrets.value = []
    void fetchOrder()
  },
  { immediate: true },
)

const TERMINAL = new Set(['completed', 'cancelled', 'expired', 'refunded'])
const offs = [
  rt.on('order', (o) => {
    if (o.id === id.value && (!order.value || o.version >= order.value.version || o.status !== order.value.status)) order.value = o
  }),
  rt.on('sync', () => {
    if (order.value && !TERMINAL.has(order.value.status)) void fetchOrder(true)
  }),
]
onBeforeUnmount(() => {
  offs.forEach((off) => off())
  clearInterval(timer)
  secrets.value = []
})

const stepIndex = computed(() => {
  switch (order.value?.status) {
    case 'pending_payment':
      return 0
    case 'paid':
    case 'manual_review':
      return 1
    case 'delivered':
      return 2
    case 'completed':
      return 3
    default:
      return -1
  }
})
const countdown = computed(() =>
  order.value?.status === 'pending_payment' && order.value.expires_at ? formatCountdown(order.value.expires_at, now.value) : null,
)
const hint = computed(() => {
  const o = order.value
  if (!o) return ''
  switch (o.status) {
    case 'paid':
      return o.delivery_mode === 'manual' ? t('order.waitingDelivery') : t('order.waitingAuto')
    case 'manual_review':
      return t('order.reviewHint')
    case 'cancelled':
      return t('order.cancelledHint')
    case 'expired':
      return t('order.expiredHint')
    case 'refunded':
      return t('order.refundedHint')
    default:
      return ''
  }
})
const channelLabel = computed(() => {
  const c = order.value?.channel
  if (!c) return '—'
  return te(`order.channels.${c}`) ? t(`order.channels.${c}`) : c
})

async function pay() {
  const o = order.value
  if (!o || paying.value) return
  paying.value = true
  try {
    const { pay_url } = await api.orders.pay(o.id)
    const safe = safePayUrl(pay_url)
    if (!safe) throw new Error('unsafe pay_url')
    showLoadingToast({ message: t('order.paying'), forbidClick: true, duration: 0 })
    window.location.assign(safe)
  } catch (e) {
    closeToast()
    toastError(e)
    void fetchOrder(true)
  } finally {
    paying.value = false
  }
}

async function reveal() {
  const o = order.value
  if (!o || revealing.value) return
  if (!(await confirm(t('order.revealTitle'), t('order.revealWarn')))) return
  revealing.value = true
  try {
    const r = await api.orders.reveal(o.id)
    secrets.value = r.secrets
  } catch (e) {
    toastError(e)
  } finally {
    revealing.value = false
  }
}
async function copy(s: string) {
  showToast((await copyText(s)) ? t('common.copied') : t('common.copyFailed'))
}

async function confirmReceived() {
  const o = order.value
  if (!o || confirming.value) return
  if (!(await confirm(t('order.confirmTitle'), t('order.confirmMessage')))) return
  confirming.value = true
  try {
    order.value = await api.orders.confirm(o.id)
    showSuccessToast(t('order.confirmed'))
  } catch (e) {
    toastError(e)
  } finally {
    confirming.value = false
  }
}

function previewAttachment(url: string) {
  showImagePreview({ images: [url], closeable: true })
}
function back() {
  if (window.history.length > 1) router.back()
  else void router.push('/orders')
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('order.title')" class="dock-top" left-arrow fixed placeholder safe-area-inset-top @click-left="back" />

    <div v-if="loading" class="section card pad">
      <div class="skeleton" style="height: 20px; width: 50%" />
      <div class="skeleton" style="height: 60px; margin-top: 16px" />
      <div class="skeleton" style="height: 16px; width: 70%; margin-top: 16px" />
    </div>

    <van-empty v-else-if="notFound || !order" image="error" :description="t('errors.not_found')">
      <van-button round type="primary" to="/orders">{{ t('orders.title') }}</van-button>
    </van-empty>

    <template v-else>
      <section class="status-card" :class="`st-${order.status}`">
        <div class="status-title">{{ t(`orderStatus.${order.status}`) }}</div>
        <div v-if="countdown" class="status-sub">{{ t('order.payWithin', { time: countdown }) }}</div>
        <div v-else-if="hint" class="status-sub">{{ hint }}</div>
      </section>

      <section v-if="stepIndex >= 0" class="section card steps">
        <van-steps :active="stepIndex">
          <van-step>{{ t('order.steps.placed') }}</van-step>
          <van-step>{{ t('order.steps.paid') }}</van-step>
          <van-step>{{ t('order.steps.delivered') }}</van-step>
          <van-step>{{ t('order.steps.completed') }}</van-step>
        </van-steps>
      </section>

      <section class="section card pad">
        <div class="o-title">{{ order.title }}</div>
        <div class="o-line">
          <span :class="order.delivery_mode === 'auto_card' ? 'badge-instant' : 'badge-manual'">
            {{ t(`deliveryMode.${order.delivery_mode}`) }}
          </span>
          <span v-if="order.quote_id" class="muted small">{{ t('order.fromQuote') }}</span>
        </div>
        <div class="o-amount">
          <span class="muted">{{ t('order.amount') }}</span>
          <span class="price big">{{ formatMoney(order.amount, order.currency, locale) }}</span>
        </div>
      </section>

      <!-- auto delivery: reveal codes -->
      <section v-if="order.can_reveal" class="section card pad codes">
        <div class="codes-head">
          <h2>{{ t('order.codes') }}</h2>
          <van-button v-if="secrets.length" size="small" round plain @click="secrets = []">{{ t('order.hide') }}</van-button>
        </div>
        <van-notice-bar wrapable :scrollable="false" left-icon="warning-o" class="warn">
          {{ t('order.revealWarn') }}
        </van-notice-bar>
        <template v-if="secrets.length">
          <p class="muted small">{{ t('order.codesHint') }}</p>
          <ul class="code-list">
            <li v-for="(s, i) in secrets" :key="i" class="code-row">
              <code class="code">{{ s }}</code>
              <van-button size="small" round type="primary" class="tap" @click="copy(s)">{{ t('common.copy') }}</van-button>
            </li>
          </ul>
          <van-button v-if="secrets.length > 1" block round plain @click="copy(secrets.join('\n'))">{{ t('order.copyAll') }}</van-button>
        </template>
        <van-button v-else block round type="primary" :loading="revealing" icon="eye-o" @click="reveal">
          {{ t('order.reveal') }}
        </van-button>
      </section>

      <!-- manual delivery -->
      <section v-if="order.manual_delivery" class="section card pad">
        <h2 class="h2">{{ t('order.manualDelivery') }}</h2>
        <p v-if="order.manual_delivery.text" class="manual-text">{{ order.manual_delivery.text }}</p>
        <template v-if="order.manual_delivery.attachment">
          <button
            v-if="order.manual_delivery.attachment.kind === 'image'"
            type="button"
            class="att-img"
            :aria-label="t('order.openAttachment')"
            @click="previewAttachment(order.manual_delivery.attachment.url)"
          >
            <img :src="order.manual_delivery.attachment.url" :alt="t('order.attachment')" loading="lazy" />
          </button>
          <a v-else :href="order.manual_delivery.attachment.url" target="_blank" rel="noopener noreferrer">{{ t('order.openAttachment') }}</a>
        </template>
      </section>

      <section class="section card clip">
        <van-cell-group :border="false">
          <van-cell :title="t('order.orderNo')">
            <template #value>
              <button type="button" class="link-btn" @click="copy(order.order_no)">{{ order.order_no }}</button>
            </template>
          </van-cell>
          <van-cell :title="t('order.unitQty')" :value="String(order.qty)" />
          <van-cell :title="t('order.mode')" :value="t(`deliveryModeLong.${order.delivery_mode}`)" />
          <van-cell :title="t('order.channel')" :value="channelLabel" />
          <van-cell :title="t('order.createdAt')" :value="formatDateTime(order.created_at, locale)" />
          <van-cell v-if="order.paid_at" :title="t('order.paidAt')" :value="formatDateTime(order.paid_at, locale)" />
          <van-cell v-if="order.delivered_at" :title="t('order.deliveredAt')" :value="formatDateTime(order.delivered_at, locale)" />
        </van-cell-group>
      </section>

      <div class="contact">
        <van-button round plain icon="chat-o" to="/chat">{{ t('order.contact') }}</van-button>
      </div>
      <div v-if="order.status === 'pending_payment' || order.status === 'delivered'" class="bar-spacer" />

      <div v-if="order.status === 'pending_payment' || order.status === 'delivered'" class="dock action-dock">
        <span class="price">{{ formatMoney(order.amount, order.currency, locale) }}</span>
        <van-button
          v-if="order.status === 'pending_payment'"
          round
          type="primary"
          class="action-btn"
          :loading="paying"
          :disabled="!countdown && !!order.expires_at"
          @click="pay"
        >
          {{ t('order.pay') }}
        </van-button>
        <van-button v-else round type="primary" class="action-btn" :loading="confirming" @click="confirmReceived">
          {{ t('order.confirm') }}
        </van-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding-bottom: calc(var(--safe-bottom) + 16px);
}
.pad {
  padding: 14px 16px;
}
.clip {
  overflow: hidden;
}
.status-card {
  margin: 0;
  padding: 18px 16px 28px;
  background: linear-gradient(180deg, var(--c-accent), #ffe680);
  color: var(--c-on-accent);
}
.status-card.st-cancelled,
.status-card.st-expired,
.status-card.st-refunded {
  background: linear-gradient(180deg, #d9d9d9, #ececec);
}
.status-card.st-completed,
.status-card.st-delivered {
  background: linear-gradient(180deg, #bff0c9, #e2f8e7);
}
.status-title {
  font-size: 20px;
  font-weight: 800;
}
.status-sub {
  margin-top: 4px;
  font-size: 13px;
  opacity: 0.85;
  font-variant-numeric: tabular-nums;
}
.steps {
  margin-top: -16px;
  position: relative;
  padding: 4px 8px;
}
.o-title {
  font-weight: 700;
  font-size: 16px;
  overflow-wrap: anywhere;
}
.o-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0 10px;
}
.o-amount {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.price.big {
  font-size: 20px;
}
.small {
  font-size: 12px;
}
.codes-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.codes h2,
.h2 {
  font-size: 15px;
  margin: 0 0 8px;
}
.warn {
  border-radius: 8px;
  margin-bottom: 12px;
  --van-notice-bar-background: #fff4e5;
  --van-notice-bar-text-color: #8a4b00;
  --van-notice-bar-font-size: 12px;
  --van-notice-bar-line-height: 18px;
}
.code-list {
  list-style: none;
  padding: 0;
  margin: 0 0 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.code-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  border-radius: 10px;
  background: #faf8f2;
  border: 1px dashed #e7d9a8;
}
.code {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 15px;
  overflow-wrap: anywhere;
  user-select: all;
}
.manual-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0 0 10px;
}
.att-img {
  padding: 0;
  border: 0;
  background: #f0eee8;
  border-radius: 10px;
  overflow: hidden;
  width: 160px;
  aspect-ratio: 1 / 1;
  cursor: zoom-in;
}
.att-img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.link-btn {
  border: 0;
  background: none;
  padding: 0;
  color: var(--c-text);
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  cursor: copy;
  overflow-wrap: anywhere;
  text-align: end;
  min-height: 24px;
}
.contact {
  display: flex;
  justify-content: center;
  margin: 16px;
}
.bar-spacer {
  height: calc(64px + var(--safe-bottom));
}
.action-dock {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px calc(8px + var(--safe-bottom));
  background: var(--c-surface);
  box-shadow: 0 -1px 0 var(--c-line);
}
.action-dock .price {
  font-size: 18px;
}
.action-btn {
  min-width: 140px;
  font-weight: 700;
}
</style>
