<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { DeliveryView } from '@24goods/shared/ui'
import { SmartText } from '@24goods/shared/chat'
import {
  BRAND,
  canRequestAfterSales,
  copyText,
  revealedCodes,
  downloadText,
  goBack,
  formatCountdown,
  formatDateTime,
  formatMoney,
  isApiError,
  safeAssetUrl,
  type AfterSales,
  type BuyerInputConfig,
  type CheckoutView,
  type Order,
} from '@24goods/shared'
import { api } from '../api'
import AfterSalesSheet from '../components/AfterSalesSheet.vue'
import AfterSalesStatus from '../components/AfterSalesStatus.vue'
import BuyerInputSheet from '../components/BuyerInputSheet.vue'
import FeeLines from '../components/FeeLines.vue'
import ManualPromise from '../components/ManualPromise.vue'
import PayChannelPicker from '../components/PayChannelPicker.vue'
import { cancelPending, payCheckout, payOrder } from '../lib/orderActions'
import { dropAccessCode, readAccessCode } from '../lib/attribution'
import { openSupport as support } from '../lib/support'
import { useRealtimeStore } from '../stores/realtime'
import { useSiteStore } from '../stores/site'
import { confirm, showImagePreview, showSuccessToast, showToast, toastError } from '../ui'

const { t, te, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const rt = useRealtimeStore()
const site = useSiteStore()

const order = ref<Order | null>(null)
const attachmentUrl = computed(() => safeAssetUrl(order.value?.manual_delivery?.attachment?.url))
const loading = ref(true)
const notFound = ref(false)
const paying = ref(false)
const revealing = ref(false)
const confirming = ref(false)
/** Revealed codes live only in memory for this view; never persisted. */
const secrets = ref<string[]>([])
const accessCode = ref<string | null>(null)
const asSheet = ref(false)
const now = ref(Date.now())
const timer = setInterval(() => (now.value = Date.now()), 1000)

const id = computed(() => String(route.params.id ?? ''))

async function fetchOrder(silent = false) {
  if (!id.value) return
  if (!silent) loading.value = true
  try {
    order.value = await api.orders.get(id.value)
    site.orderStoreName = order.value.reseller?.store_name ?? null
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
    accessCode.value = readAccessCode(id.value)
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
void site.loadChannels()
onBeforeUnmount(() => {
  offs.forEach((off) => off())
  clearInterval(timer)
  site.orderStoreName = null
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
/** Fee/total of the channel the buyer has selected NOW: server-computed (order.channel_totals), never
 *  client math. Falls back to the stored order values (paid orders / older payloads). */
const live = computed(() => {
  const o = order.value
  const ct = o?.status === 'pending_payment' ? o.channel_totals?.[site.channel?.id ?? ''] : undefined
  return { fee: ct?.fee ?? o?.fee ?? '0.00', rate: ct?.fee_rate ?? o?.fee_rate, amount: ct?.amount ?? o?.amount ?? '0.00' }
})
const liveCheckoutAmount = computed(() => {
  const c = checkout.value
  return c?.channel_totals?.[site.channel?.id ?? '']?.amount ?? c?.amount ?? '0.00'
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
    await payOrder(o)
  } catch {
    void fetchOrder(true)
  } finally {
    paying.value = false
  }
}
async function payTogether() {
  const no = order.value?.checkout_no
  if (!no || paying.value) return
  paying.value = true
  try {
    await payCheckout(no)
  } catch {
    void fetchOrder(true)
  } finally {
    paying.value = false
  }
}
const cancelling = ref(false)
async function cancelIt() {
  const o = order.value
  if (!o || cancelling.value) return
  cancelling.value = true
  try {
    await cancelPending(o, checkout.value?.order_nos.length ?? 0)
  } finally {
    cancelling.value = false
    void fetchOrder(true)
  }
}

// ---- combined checkout (siblings) ----
const checkout = ref<CheckoutView | null>(null)
const siblings = ref<Order[]>([])
watch(
  () => order.value?.checkout_no,
  async (no) => {
    checkout.value = null
    siblings.value = []
    if (!no) return
    try {
      const c = await api.checkout.get(no)
      checkout.value = c
      const others = c.order_nos.filter((n) => n !== order.value?.order_no).slice(0, 20)
      siblings.value = (await Promise.all(others.map((n) => api.orders.get(n).catch(() => null)))).filter(
        (x): x is Order => !!x,
      )
    } catch {
      /* optional block */
    }
  },
  { immediate: true },
)

// ---- order information (buyer_input) ----
const infoSheet = ref(false)
const infoBusy = ref(false)
const infoConfig = ref<BuyerInputConfig | null>(null)
const canEditInput = computed(() => order.value?.can_edit_input === true)
async function editInput() {
  const cfg = order.value?.input_config
  if (!canEditInput.value || !cfg?.enabled) return
  infoConfig.value = { ...cfg, prompt: '' }
  infoSheet.value = true
}
async function saveInput(v: string) {
  const o = order.value
  if (!o || infoBusy.value) return
  infoBusy.value = true
  try {
    order.value = await api.orders.setInput(o.id, v)
    infoSheet.value = false
    showSuccessToast(t('orderInfo.saved'))
  } catch (e) {
    toastError(e)
  } finally {
    infoBusy.value = false
  }
}

async function reveal() {
  const o = order.value
  if (!o || revealing.value) return
  if (!(await confirm(t('delivery.revealTitle'), t('delivery.revealWarn')))) return
  revealing.value = true
  try {
    const r = await api.orders.reveal(o.id)
    secrets.value = revealedCodes(r)
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
  goBack(router, '/orders')
}
const canAfterSales = computed(() => !!order.value && canRequestAfterSales(order.value))
const asExpired = computed(
  () => !!order.value && ['delivered', 'completed'].includes(order.value.status) && !canAfterSales.value && !order.value.after_sales,
)
function ticketCreated(tk: AfterSales) {
  if (order.value) order.value = { ...order.value, after_sales: tk }
}
function saveAccessCode() {
  const o = order.value
  if (!o || !accessCode.value) return
  downloadText(
    `${o.order_no}.txt`,
    `${site.headerName}\n${t('order.orderNo')}: ${o.order_no}\n${t('lookup.accessCode')}: ${accessCode.value}\n${t('lookup.saveNote', { url: `${site.whiteLabel ? window.location.host : BRAND.domain}/lookup` })}\n`,
  )
}
function gotCode() {
  dropAccessCode(id.value)
  accessCode.value = null
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
          <span v-if="order.delivery_mode === 'auto_card'" class="badge-instant">
            {{ t(`deliveryMode.${order.delivery_mode}`) }}
          </span>
          <ManualPromise v-else :eta="order.delivery_eta_hours" compact />
          <span v-if="order.reseller" class="muted small">{{ t('order.viaStore', { name: order.reseller.store_name }) }}</span>
          <span v-if="order.quote_id" class="muted small">{{ t('order.fromQuote') }}</span>
        </div>
        <PayChannelPicker v-if="order.status === 'pending_payment'" />
        <FeeLines
          v-if="order.subtotal && order.fee"
          class="o-fees"
          :subtotal="order.subtotal"
          :discount-minor="order.discount_minor"
          :fee="live.fee"
          :currency="order.currency"
          :label="site.channel?.label"
          :rate="live.rate"
          hide-when-free
        />
        <div v-else class="o-amount">
          <span class="muted">{{ t('order.amount') }}</span>
          <span class="price big">{{ formatMoney(live.amount, order.currency, locale) }}</span>
        </div>
        <p v-if="order.status === 'cancelled' && order.cancel_reason" class="muted small reason">
          {{ t('cancel.reason', { reason: order.cancel_reason }) }}
        </p>
      </section>

      <section v-if="order.buyer_input || canEditInput" class="section card pad">
        <div class="bi-head">
          <h2 class="h2">{{ t('orderInfo.title') }}</h2>
          <span class="bi-actions">
            <button v-if="order.buyer_input" type="button" class="link-act" @click="copy(order.buyer_input)">{{ t('common.copy') }}</button>
            <button v-if="canEditInput && order.product_id" type="button" class="link-act" @click="editInput">{{ t('orderInfo.edit') }}</button>
          </span>
        </div>
        <p v-if="order.buyer_input" class="bi-value"><SmartText :text="order.buyer_input" /></p>
        <p v-else class="muted small">{{ t('orderInfo.empty') }}</p>
      </section>

      <section v-if="order.checkout_no" class="section card pad">
        <h2 class="h2">{{ t('cancel.siblings') }}</h2>
        <p class="muted small">{{ t('cancel.inCheckout', { no: order.checkout_no }) }}</p>
        <RouterLink
          v-for="s in siblings"
          :key="s.id"
          :to="`/orders/${encodeURIComponent(s.id)}`"
          class="sib"
        >
          <span class="sib-title">{{ s.title }}</span>
          <span class="price">{{ formatMoney(s.amount, s.currency, locale) }}</span>
        </RouterLink>
        <div v-if="checkout" class="sib-total">
          <span class="muted">{{ t('cancel.checkoutTotal') }}</span>
          <span class="price">{{ formatMoney(checkout.amount, checkout.currency, locale) }}</span>
        </div>
      </section>

      <section v-if="accessCode" class="section card pad access">
        <h2 class="h2">{{ t('lookup.yourCode') }}</h2>
        <p class="muted small">{{ t('lookup.codeNote') }}</p>
        <div class="ac-row">
          <code class="ac">{{ accessCode }}</code>
          <van-button size="small" round type="primary" class="tap" @click="copy(accessCode)">{{ t('common.copy') }}</van-button>
        </div>
        <div class="ac-actions">
          <van-button size="small" round plain class="tap" @click="saveAccessCode">{{ t('cred.save') }}</van-button>
          <van-button size="small" round plain class="tap" @click="gotCode">{{ t('lookup.gotIt') }}</van-button>
        </div>
      </section>

      <section v-if="order.can_reveal || secrets.length || order.manual_delivery || ['delivered', 'completed'].includes(order.status)" class="section card pad">
        <DeliveryView
          :info="order.delivery_info"
          :codes="secrets"
          :can-reveal="order.can_reveal"
          :revealing="revealing"
          :manual-text="order.manual_delivery?.text ?? null"
          :order-no="order.order_no"
          :after-sales-until="order.after_sales_until"
          :auto-confirm-at="order.status === 'delivered' ? order.auto_confirm_at : null"
          :can-confirm="order.status === 'delivered'"
          :confirming="confirming"
          @reveal="reveal"
          @hide="secrets = []"
          @confirm="confirmReceived"
        />
        <template v-if="order.manual_delivery?.attachment && attachmentUrl">
          <button
            v-if="order.manual_delivery.attachment.kind === 'image'"
            type="button"
            class="att-img"
            :aria-label="t('order.openAttachment')"
            @click="previewAttachment(attachmentUrl)"
          >
            <img :src="attachmentUrl" :alt="t('order.attachment')" loading="lazy" />
          </button>
          <a v-else :href="attachmentUrl" target="_blank" rel="noopener noreferrer">{{ t('order.openAttachment') }}</a>
        </template>
      </section>

      <section v-if="order.after_sales || canAfterSales || asExpired" class="section card pad">
        <AfterSalesStatus v-if="order.after_sales" :ticket="order.after_sales" />
        <van-button v-if="canAfterSales" block round plain class="as-btn" @click="asSheet = true">{{ t('afterSales.request') }}</van-button>
        <p v-else-if="asExpired" class="muted small">{{ t('afterSales.expired') }}</p>
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
          <van-cell
            v-if="order.delivery_mode === 'manual' && order.paid_at && !order.delivered_at && order.delivery_deadline"
            :title="t('manual.deadline')"
            :value="formatDateTime(order.delivery_deadline, locale)"
          />
        </van-cell-group>
      </section>

      <div class="contact">
        <van-button round plain icon="chat-o" @click="support(router)">{{ t('order.contact') }}</van-button>
      </div>
      <div class="contact">
        <RouterLink v-if="site.features.lookup" to="/lookup" class="lk">{{ t('lookup.title') }}</RouterLink>
      </div>
      <div v-if="order.status === 'pending_payment'" class="bar-spacer" />

      <div v-if="order.status === 'pending_payment'" class="dock action-dock">
        <div class="pay-info">
          <span class="price">{{ formatMoney(checkout && order.checkout_no ? liveCheckoutAmount : live.amount, order.currency, locale) }}</span>
          <span v-if="countdown" class="cd">{{ t('order.payWithin', { time: countdown }) }}</span>
        </div>
        <van-button round plain class="cancel-btn" :loading="cancelling" @click="cancelIt">{{ t('cancel.action') }}</van-button>
        <van-button
          round
          type="primary"
          class="action-btn"
          :loading="paying"
          :disabled="!countdown && !!order.expires_at"
          @click="order.checkout_no ? payTogether() : pay()"
        >
          {{ order.checkout_no ? t('cancel.payTogether', { n: checkout?.order_nos.length ?? siblings.length + 1 }) : t('order.pay') }}
        </van-button>
      </div>
      <BuyerInputSheet v-model:show="infoSheet" :config="infoConfig" :initial="order.buyer_input" :busy="infoBusy" @confirm="saveInput" />
      <AfterSalesSheet v-model:show="asSheet" :order-id="order.id" @created="ticketCreated" />
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
  min-width: 120px;
  font-weight: 700;
}
.pay-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.cd {
  font-size: 12px;
  color: var(--c-text-2);
  font-variant-numeric: tabular-nums;
}
.cancel-btn {
  flex: none;
}
.o-fees {
  margin-top: 8px;
}
.reason {
  margin: 8px 0 0;
}
.bi-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.bi-actions {
  display: flex;
  gap: 4px;
}
.link-act {
  min-height: 44px;
  padding: 0 8px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.bi-value {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 200px;
  overflow: auto;
  font-size: 13px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.sib {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  text-decoration: none;
  color: inherit;
  border-top: 1px solid var(--c-line);
}
.sib-title {
  min-width: 0;
  overflow-wrap: anywhere;
}
.sib-total {
  display: flex;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--c-line);
}
.ac-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  border-radius: 10px;
  background: #faf8f2;
  border: 1px dashed #e7d9a8;
}
.ac {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 18px;
  letter-spacing: 0.08em;
  user-select: all;
}
.ac-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.as-btn {
  margin-top: 8px;
}
.lk {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  color: var(--c-text-2);
  font-size: 13px;
}
</style>
