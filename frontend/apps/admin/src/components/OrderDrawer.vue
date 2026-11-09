<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UploadRequestOptions } from 'element-plus'
import {
  compareAmount,
  compressImage,
  copyText,
  formatCountdown,
  formatDateTime,
  formatRelative,
  formatMoney,
  imageExtension,
  isValidAmount,
  multiplyAmount,
  normalizeAmount,
  safeAssetUrl,
  usePasteImage,
  visibleEmail,
  type Attachment,
  type Order,
} from '@24goods/shared'
import { api, apiV4 } from '../api'
import type { AdminOrderDetailV4 as AdminOrderDetailBase, SiblingOrder } from '../api-v4'
import type { OrderV5Fields } from '../api-v5'
import { isValidRefundReference } from '../lib/afterSales'
import { useAuthStore } from '../stores/auth'
import { marginMinor, minorToYuan, yuanToMinor } from '../lib/money'
import CloseOrderDialog from './CloseOrderDialog.vue'
import { buildBuyerTemplate, clockTime, htmlToText } from '../lib/orderText'
import OrderStatusTag from './OrderStatusTag.vue'
import { confirmBox, toastError, toastOk } from '../ui'

type AdminOrderDetail = AdminOrderDetailBase & Partial<OrderV5Fields>
const props = defineProps<{ orderId: string | null }>()
const emit = defineEmits<{ close: []; updated: [o: Order] }>()
const { t, locale } = useI18n()
const PAY_CHANNELS = ['zpay_alipay', 'usdt_xlayer', 'usdt_arbitrum', 'usdt_trc20']
function channelLabel(c: string): string {
  return PAY_CHANNELS.includes(c) ? t(`payChannel.${c}`) : c
}

const order = ref<AdminOrderDetail | null>(null)
const loading = ref(false)
const now = ref(Date.now())
let tick: ReturnType<typeof setInterval> | null = null
onMounted(() => (tick = setInterval(() => (now.value = Date.now()), 15_000)))
onBeforeUnmount(() => tick && clearInterval(tick))
const deliveredAgo = computed(() => (order.value?.delivered_at ? `${t('orders.deliveredAt', { time: clockTime(order.value.delivered_at, locale.value) })} · ${formatRelative(order.value.delivered_at, locale.value, now.value)}` : ''))
const autoConfirm = computed(() => {
  const o = order.value
  if (!o?.auto_confirm_at || o.status !== 'delivered') return null
  if (o.after_sales && (o.after_sales.status === 'open' || o.after_sales.status === 'processing')) return t('orders.autoConfirmPaused')
  return formatCountdown(o.auto_confirm_at, now.value) ?? t('orders.autoConfirmDue')
})
async function copyDelivery() {
  const text = order.value?.manual_delivery?.text
  if (text) toastCopy(await copyText(text))
}
async function copyBuyerMessage() {
  const o = order.value
  if (!o?.manual_delivery) return
  const di = o.delivery_info
  const text = buildBuyerTemplate({
    orderNo: o.order_no,
    title: o.title,
    content: o.manual_delivery.text ?? "",
    usage: htmlToText(di?.usage_html),
    redeemUrl: di?.redeem_url,
    afterSalesUntil: o.after_sales_until,
    labels: {
      order: t('orders.tpl.order'),
      item: t('orders.tpl.item'),
      content: t('orders.tpl.content'),
      usage: t('orders.tpl.usage'),
      redeem: t('orders.tpl.redeem'),
      afterSales: t('orders.tpl.afterSales'),
      thanks: t('orders.tpl.thanks'),
    },
    formatDate: (iso) => formatDateTime(iso, locale.value),
  })
  toastCopy(await copyText(text))
}
function toastCopy(ok: boolean) {
  if (ok) toastOk(t('common.copied'))
  else toastError(new Error('copy'))
}
const floorUnit = ref<string | null>(null)

async function load(id: string) {
  loading.value = true
  floorUnit.value = null
  try {
    order.value = await apiV4.orders.get(id)
    void resolveFloor(order.value)
    void loadSiblings(order.value)
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
watch(
  () => props.orderId,
  (id) => {
    order.value = null
    if (id) void load(id)
  },
  { immediate: true },
)

/** Floor price per unit: from the (optional) detail field, else looked up from the product list. */
async function resolveFloor(o: AdminOrderDetail) {
  if (o.floor_price) {
    floorUnit.value = o.floor_price
    return
  }
  if (!o.product_id) return
  try {
    const r = await api.products.list({ q: o.title, page: 1, page_size: 50 })
    floorUnit.value = r.items.find((p) => p.id === o.product_id)?.floor_price ?? null
  } catch {
    floorUnit.value = null
  }
}
const floorTotal = computed(() => (floorUnit.value && order.value ? multiplyAmount(floorUnit.value, order.value.qty) : null))
const yuanMinor = (m: number | null | undefined) => (m == null ? null : minorToYuan(m))
/** Net of discount and reseller markup, minus staff-only cost (cost_minor is the order's total cost). */
const netMargin = computed(() => {
  const o = order.value
  if (!o || o.cost_minor == null) return null
  const sub = yuanToMinor(o.subtotal ?? o.amount)
  if (sub === null) return null
  return marginMinor(sub - (o.discount_minor ?? 0) - (o.reseller_markup_minor ?? 0), o.cost_minor)
})
const money = (a: string | null | undefined) => formatMoney(a, order.value?.currency ?? 'CNY', locale.value)

/** Merge an action result (Order) into the loaded detail, keeping buyer + events, then refetch events. */
function applyUpdate(o: Order) {
  if (order.value) order.value = { ...order.value, ...(o as Partial<typeof order.value>) }
  emit('updated', o)
  if (props.orderId) void load(props.orderId)
}

// ---- change price ----
const priceDlg = ref(false)
const price = reactive({ amount: '', reason: '', removeMarkup: false, busy: false, error: '' })
const hasMarkup = computed(() => (order.value?.reseller_markup_minor ?? 0) > 0)
/** Live preview mirroring the server: promo dropped, a discount comes out of the markup first
 * (base = current subtotal − markup), fee rounded up. */
const preview = computed(() => {
  const o = order.value
  const sub = isValidAmount(price.amount) ? yuanToMinor(normalizeAmount(price.amount)) : null
  if (!o || sub === null) return null
  const oldMarkup = o.reseller_markup_minor ?? 0
  const base = (yuanToMinor(o.subtotal) ?? 0) - oldMarkup
  const markup = price.removeMarkup ? 0 : Math.max(0, Math.min(oldMarkup, sub - base))
  const permille = Math.round((parseFloat(o.fee_rate ?? '0') || 0) * 10)
  const fee = Math.ceil((sub * permille) / 1000)
  return { subtotal: sub, markup, fee, total: sub + fee }
})
const currentBreakdown = computed(() => {
  const o = order.value
  if (!o) return null
  return {
    subtotal: yuanToMinor(o.subtotal ?? o.amount) ?? 0,
    discount: o.discount_minor ?? 0,
    markup: o.reseller_markup_minor ?? 0,
    fee: yuanToMinor(o.fee ?? '0') ?? 0,
    total: yuanToMinor(o.amount) ?? 0,
  }
})
const moneyMinor = (m: number) => money(minorToYuan(m))
function openPrice() {
  price.amount = order.value?.subtotal ?? order.value?.amount ?? ''
  price.removeMarkup = false
  price.reason = ''
  price.error = ''
  priceDlg.value = true
}
async function submitPrice() {
  const o = order.value
  if (!o || price.busy) return
  if (!isValidAmount(price.amount)) {
    price.error = t('products.v.priceInvalid')
    return
  }
  if (floorTotal.value && compareAmount(price.amount, floorTotal.value) === -1) {
    price.error = t('orders.belowFloor')
    return
  }
  if (!price.reason.trim()) {
    price.error = t('orders.reasonRequired')
    return
  }
  price.busy = true
  try {
    applyUpdate(await api.orders.changePrice(o.id, normalizeAmount(price.amount)!, price.reason.trim(), price.removeMarkup))
    toastOk(t('orders.priceChanged'))
    priceDlg.value = false
  } catch (e) {
    toastError(e)
  } finally {
    price.busy = false
  }
}

// ---- manual deliver ----
const deliverDlg = ref(false)
const deliver = reactive({ text: '', attachment: null as Attachment | null, uploading: false, busy: false, error: '' })
function openDeliver() {
  deliver.text = ''
  deliver.attachment = null
  deliver.error = ''
  deliverDlg.value = true
}
function uploadAttachment(opt: UploadRequestOptions) {
  return uploadDeliverFile(opt.file)
}
usePasteImage(window, (files) => void uploadDeliverFile(files[0]), deliverDlg)
async function uploadDeliverFile(file: File) {
  if (deliver.uploading) return
  deliver.uploading = true
  try {
    const c = await compressImage(file)
    deliver.attachment = await api.upload(c.blob, { kind: 'image', filename: `delivery.${imageExtension(c.blob.type || file.type)}` })
  } catch (e) {
    toastError(e)
  } finally {
    deliver.uploading = false
  }
}
async function submitDeliver() {
  const o = order.value
  if (!o || deliver.busy) return
  if (!deliver.text.trim()) {
    deliver.error = t('orders.textRequired')
    return
  }
  deliver.busy = true
  try {
    applyUpdate(await api.orders.deliver(o.id, deliver.text.trim(), deliver.attachment?.id))
    toastOk(t('orders.delivered'))
    deliverDlg.value = false
  } catch (e) {
    toastError(e)
  } finally {
    deliver.busy = false
  }
}

// ---- close order (§3) ----
const closeDlg = ref(false)
function onClosed(o: Order) {
  applyUpdate(o)
}

// ---- sibling orders of the same checkout (§2/§3) ----
const siblings = ref<SiblingOrder[]>([])
async function loadSiblings(o: AdminOrderDetail) {
  siblings.value = []
  if (!o.checkout_no) return
  if (o.checkout_orders?.length) {
    siblings.value = o.checkout_orders.filter((x) => x.id !== o.id)
    return
  }
  try {
    // best effort: the admin search may match the checkout number
    const r = await api.orders.list({ q: o.checkout_no, page: 1, page_size: 25 })
    siblings.value = (r.items as Array<SiblingOrder & { checkout_no?: string | null }>).filter((x) => x.checkout_no === o.checkout_no && x.id !== o.id)
  } catch {
    siblings.value = []
  }
}
async function copyInput() {
  const v = order.value?.buyer_input
  if (v) toastCopy(await copyText(v))
}

// ---- v8: requeue a held auto-card order / refund before delivery (admin only) ----
const auth = useAuthStore()
const canRequeue = computed(() => auth.isAdmin && order.value?.status === 'manual_review' && order.value.delivery_mode === 'auto_card')
const canRefund = computed(() => auth.isAdmin && (order.value?.status === 'paid' || order.value?.status === 'manual_review') && order.value.kind !== 'reseller_signup')
const requeueBusy = ref(false)
async function requeue() {
  const o = order.value
  if (!o || requeueBusy.value) return
  if (!(await confirmBox(t('orders.requeueConfirm'), t('orders.requeue')))) return
  requeueBusy.value = true
  try {
    applyUpdate(await apiV4.orders.requeueDelivery(o.id))
    toastOk(t('orders.requeued'))
  } catch (e) {
    toastError(e)
  } finally {
    requeueBusy.value = false
  }
}
const refundDlg = ref(false)
const refund = reactive({ reference: '', note: '', busy: false, error: '' })
function openRefund() {
  refund.reference = ''
  refund.note = ''
  refund.error = ''
  refundDlg.value = true
}
async function submitRefund() {
  const o = order.value
  if (!o || refund.busy) return
  if (!isValidRefundReference(refund.reference)) {
    refund.error = t('afterSales.v.referenceRequired')
    return
  }
  if (!(await confirmBox(t('orders.refundConfirm'), t('orders.refund')))) return
  refund.busy = true
  try {
    applyUpdate(await apiV4.orders.refund(o.id, { reference: refund.reference.trim(), note: refund.note.trim() || undefined }))
    toastOk(t('orders.refunded'))
    refundDlg.value = false
  } catch (e) {
    toastError(e)
  } finally {
    refund.busy = false
  }
}

const canChangePrice = computed(() => order.value?.status === 'pending_payment')
const canClose = computed(() => order.value?.status === 'pending_payment')
const canDeliver = computed(() => order.value?.status === 'paid' && order.value.delivery_mode === 'manual')

defineExpose({ reload: () => props.orderId && load(props.orderId) })
</script>

<template>
  <el-drawer
    :model-value="!!orderId"
    :title="order ? t('orders.detail', { no: order.order_no }) : ''"
    size="min(560px, 100vw)"
    @close="emit('close')"
  >
    <div v-loading="loading" class="drawer">
      <template v-if="order">
        <div class="head">
          <OrderStatusTag :status="order.status" />
          <span class="price big">{{ money(order.amount) }}</span>
        </div>
        <div class="actions">
          <el-button v-if="canChangePrice" type="primary" plain @click="openPrice">{{ t('orders.changePrice') }}</el-button>
          <el-button v-if="canDeliver" type="success" @click="openDeliver">{{ t('orders.deliver') }}</el-button>
          <el-button v-if="canRequeue" type="warning" plain :loading="requeueBusy" @click="requeue">{{ t('orders.requeue') }}</el-button>
          <el-button v-if="canRefund" type="danger" plain @click="openRefund">{{ t('orders.refund') }}</el-button>
          <el-button v-if="canClose" type="danger" plain @click="closeDlg = true">{{ t('orders.close') }}</el-button>
        </div>

        <el-descriptions :column="1" border size="small" class="desc">
          <el-descriptions-item :label="t('orders.col.title')">{{ order.title }}</el-descriptions-item>
          <el-descriptions-item v-if="order.subtotal" :label="t('orders.info.subtotal')">{{ money(order.subtotal) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.fee && order.fee !== '0' && order.fee !== '0.00'" :label="t('orders.info.fee', { rate: order.fee_rate ?? '' })">{{ money(order.fee) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.discount_minor" :label="t('v5.orders.discount')">
            −{{ money(yuanMinor(order.discount_minor)) }}<template v-if="order.promo_code"> · <span class="mono">{{ order.promo_code }}</span></template>
          </el-descriptions-item>
          <el-descriptions-item v-if="order.reseller" :label="t('v5.orders.store')">{{ order.reseller.store_name }} <span class="mono muted">({{ order.reseller.slug }})</span></el-descriptions-item>
          <el-descriptions-item v-if="order.reseller_markup_minor" :label="t('v5.orders.markup')">{{ money(yuanMinor(order.reseller_markup_minor)) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.cost_minor != null" :label="t('v5.orders.cost')">{{ money(yuanMinor(order.cost_minor)) }}</el-descriptions-item>
          <el-descriptions-item v-if="netMargin !== null" :label="t('v5.orders.margin')"><b :class="netMargin < 0 ? 'neg' : 'pos'">{{ money(yuanMinor(netMargin)) }}</b></el-descriptions-item>
          <el-descriptions-item v-if="order.kind === 'reseller_signup'" :label="t('v5.orders.kind')"><el-tag size="small" type="warning">{{ t('v5.orders.kindSignup') }}</el-tag></el-descriptions-item>
          <el-descriptions-item v-if="order.delivery_deadline" :label="t('v5.orders.deadline')">
            {{ formatDateTime(order.delivery_deadline, locale) }}<el-tag v-if="order.overdue_manual" size="small" type="danger" class="ml">{{ t('v5.orders.overdue') }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="order.subtotal" :label="t('orders.info.total')"><b class="price">{{ money(order.amount) }}</b></el-descriptions-item>
          <el-descriptions-item v-if="order.checkout_no" :label="t('orders.info.checkout')">
            <span class="mono small">{{ order.checkout_no }}</span>
            <div v-for="s in siblings" :key="s.id" class="small"><span class="mono">{{ s.order_no }}</span> · {{ s.title }} · {{ money(s.amount) }} · {{ t(`orderStatus.${s.status}`) }}</div>
          </el-descriptions-item>
          <el-descriptions-item v-if="order.refund_reference" :label="t('orders.info.refundReference')"><span class="pre mono">{{ order.refund_reference }}</span></el-descriptions-item>
          <el-descriptions-item v-if="order.cancel_reason" :label="t('orders.info.cancelReason')"><span class="pre">{{ order.cancel_reason }}</span></el-descriptions-item>
          <el-descriptions-item v-if="order.buyer_input" :label="t('orders.info.buyerInput')">
            <p class="pre mono">{{ order.buyer_input }}</p>
            <el-button size="small" @click="copyInput">{{ t('orders.copy') }}</el-button>
          </el-descriptions-item>
          <el-descriptions-item :label="t('orders.col.buyer')">{{ order.buyer.display_name }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.buyerEmail')">{{ visibleEmail(order.buyer.email) || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.qty')">{{ order.qty }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.col.mode')">{{ t(`deliveryMode.${order.delivery_mode}`) }}</el-descriptions-item>
          <el-descriptions-item :label="t('payChannel.column')">
            <el-tag v-if="order.channel" size="small" :type="String(order.channel).startsWith('usdt_') ? 'success' : 'primary'" effect="plain">{{ channelLabel(order.channel) }}</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">{{ t('payChannel.none') }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.version')">v{{ order.version }}</el-descriptions-item>
          <el-descriptions-item v-if="order.quote_id" :label="t('orders.info.quote')"><span class="mono small">{{ order.quote_id }}</span></el-descriptions-item>
          <el-descriptions-item :label="t('orders.col.created')">{{ formatDateTime(order.created_at, locale) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.expires_at" :label="t('orders.info.expiresAt')">{{ formatDateTime(order.expires_at, locale) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.paid_at" :label="t('orders.info.paidAt')">{{ formatDateTime(order.paid_at, locale) }}</el-descriptions-item>
          <el-descriptions-item v-if="order.delivered_at" :label="t('orders.info.deliveredAt')">
            {{ formatDateTime(order.delivered_at, locale) }}<div class="muted small" aria-live="off">{{ deliveredAgo }}</div>
          </el-descriptions-item>
          <el-descriptions-item v-if="autoConfirm" :label="t('orders.info.autoConfirm')">{{ autoConfirm }}</el-descriptions-item>
          <el-descriptions-item v-if="order.after_sales_until" :label="t('orders.info.afterSalesUntil')">
            {{ formatDateTime(order.after_sales_until, locale) }}
            <span class="muted small"> · {{ formatRelative(order.after_sales_until, locale, now) }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="order.after_sales" :label="t('nav.afterSales')">
            <RouterLink :to="{ path: '/after-sales', query: { open: order.after_sales.id } }">{{ t(`afterSales.status.${order.after_sales.status}`) }}</RouterLink>
          </el-descriptions-item>
          <el-descriptions-item v-if="order.manual_delivery" :label="t('orders.info.manualDelivery')">
            <p class="pre">{{ order.manual_delivery.text }}</p>
            <div class="copy-row">
              <el-button size="small" @click="copyDelivery">{{ t('orders.copyText') }}</el-button>
              <el-button size="small" @click="copyBuyerMessage">{{ t('orders.copyBuyer') }}</el-button>
            </div>
            <el-image
              v-if="order.manual_delivery.attachment?.kind === 'image'"
              :src="safeAssetUrl(order.manual_delivery.attachment.url) ?? undefined"
              :preview-src-list="[safeAssetUrl(order.manual_delivery.attachment.url) ?? '']"
              fit="cover"
              class="att"
              preview-teleported
            />
          </el-descriptions-item>
        </el-descriptions>

        <h3 class="h3">{{ t('orders.timeline') }}</h3>
        <el-timeline v-if="order.events.length">
          <el-timeline-item v-for="(ev, i) in order.events" :key="i" :timestamp="formatDateTime(ev.at, locale)" placement="top">
            <div>
              <template v-if="ev.from">{{ t(`orderStatus.${ev.from}`) }} → </template>{{ t(`orderStatus.${ev.to}`) }}
              <span class="muted small"> · {{ t('orders.by', { actor: ev.actor }) }}</span>
            </div>
            <div v-if="ev.reason" class="muted small pre">{{ ev.reason }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else :description="t('orders.noEvents')" :image-size="60" />
      </template>
    </div>

    <el-dialog v-model="priceDlg" :title="t('orders.changePrice')" width="min(440px, 94vw)" append-to-body>
      <el-form label-position="top" @submit.prevent="submitPrice">
        <el-form-item :label="`${t('orders.newAmount')} (${order?.currency})`" :error="price.error">
          <el-input v-model="price.amount" inputmode="decimal" />
          <div class="small muted hint">
            <template v-if="floorUnit && floorTotal">{{ t('orders.floor', { floor: money(floorUnit), qty: order?.qty ?? 1, total: money(floorTotal) }) }}</template>
            <template v-else>{{ t('orders.floorUnknown') }}</template>
          </div>
        </el-form-item>
        <el-form-item v-if="hasMarkup">
          <el-checkbox v-model="price.removeMarkup">{{ t('orders.reprice.removeMarkup') }}</el-checkbox>
        </el-form-item>
        <el-descriptions v-if="currentBreakdown" :title="t('orders.reprice.current')" :column="1" size="small" border>
          <el-descriptions-item :label="t('orders.reprice.subtotal')">{{ moneyMinor(currentBreakdown.subtotal) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.discount')">{{ moneyMinor(currentBreakdown.discount) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.markup')">{{ moneyMinor(currentBreakdown.markup) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.fee')">{{ moneyMinor(currentBreakdown.fee) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.total')"><b>{{ moneyMinor(currentBreakdown.total) }}</b></el-descriptions-item>
        </el-descriptions>
        <el-descriptions v-if="preview" class="reprice-preview" :title="t('orders.reprice.result')" :column="1" size="small" border>
          <el-descriptions-item :label="t('orders.reprice.subtotal')">{{ moneyMinor(preview.subtotal) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.discount')">{{ moneyMinor(0) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.markup')">{{ moneyMinor(preview.markup) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.fee')">{{ moneyMinor(preview.fee) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.reprice.total')"><b>{{ moneyMinor(preview.total) }}</b></el-descriptions-item>
        </el-descriptions>
        <div class="small muted hint">{{ t('orders.reprice.note') }}</div>
        <el-form-item :label="t('orders.reason')" class="reprice-reason">
          <el-input v-model="price.reason" :placeholder="t('orders.reasonPlaceholder')" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="priceDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="price.busy" @click="submitPrice">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deliverDlg" :title="t('orders.deliver')" width="min(520px, 94vw)" append-to-body>
      <el-form label-position="top" @submit.prevent="submitDeliver">
        <el-form-item :label="t('orders.deliverText')" :error="deliver.error">
          <el-input v-model="deliver.text" type="textarea" :rows="6" :placeholder="t('orders.deliverPlaceholder')" maxlength="4000" show-word-limit />
        </el-form-item>
        <el-form-item :label="t('orders.attachment')">
          <el-upload :show-file-list="false" :http-request="uploadAttachment" accept="image/jpeg,image/png,image/webp">
            <el-button :loading="deliver.uploading">{{ t('products.form.upload') }}</el-button>
          </el-upload>
          <div class="hint muted full">{{ t('paste.tip') }}</div>
          <el-image v-if="deliver.attachment" :src="safeAssetUrl(deliver.attachment.url) ?? undefined" fit="cover" class="att" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deliverDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="success" :loading="deliver.busy" @click="submitDeliver">{{ t('orders.deliver') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="refundDlg" :title="t('orders.refund')" width="min(460px, 94vw)" append-to-body>
      <el-alert type="warning" :closable="false" show-icon :title="t('orders.refundHint')" class="gap" />
      <el-form label-position="top" @submit.prevent="submitRefund">
        <el-form-item :label="t('afterSales.refundReference')" :error="refund.error">
          <el-input v-model="refund.reference" maxlength="200" show-word-limit :placeholder="t('afterSales.refundReferenceHint')" />
          <div class="small muted hint">{{ t('afterSales.refundReferenceVisible') }}</div>
        </el-form-item>
        <el-form-item :label="t('afterSales.note')">
          <el-input v-model="refund.note" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="refundDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="refund.busy" @click="submitRefund">{{ t('afterSales.refundConfirmBtn') }}</el-button>
      </template>
    </el-dialog>

    <CloseOrderDialog v-model="closeDlg" :order-id="order?.id ?? null" :checkout-no="order?.checkout_no" @closed="onClosed" />
  </el-drawer>
</template>

<style scoped>
.pos {
  color: var(--el-color-success);
}
.neg {
  color: var(--el-color-danger);
}
.ml {
  margin-left: 8px;
}
.drawer {
  min-height: 200px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.reprice-preview,
.reprice-reason {
  margin-top: 12px;
}
.price.big {
  font-size: 22px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0 16px;
}
.actions:empty {
  display: none;
}
.desc {
  margin-bottom: 16px;
}
.h3 {
  font-size: 14px;
  margin: 16px 0 10px;
}
.small {
  font-size: 12px;
}
.hint {
  margin-top: 4px;
  line-height: 1.4;
}
.copy-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.gap {
  margin-bottom: 12px;
}
.att {
  width: 96px;
  height: 96px;
  border-radius: 8px;
  margin-top: 8px;
}
</style>
