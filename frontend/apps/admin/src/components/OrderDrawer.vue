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
  type Attachment,
  type Order,
} from '@24goods/shared'
import { api, apiV3 } from '../api'
import type { AdminOrderDetailV3 as AdminOrderDetail } from '../api-v3'
import { buildBuyerTemplate, clockTime, htmlToText } from '../lib/orderText'
import OrderStatusTag from './OrderStatusTag.vue'
import { toastError, toastOk } from '../ui'

const props = defineProps<{ orderId: string | null }>()
const emit = defineEmits<{ close: []; updated: [o: Order] }>()
const { t, locale } = useI18n()

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
    order.value = await apiV3.orders.get(id)
    void resolveFloor(order.value)
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
const money = (a: string | null | undefined) => formatMoney(a, order.value?.currency ?? 'CNY', locale.value)

/** Merge an action result (Order) into the loaded detail, keeping buyer + events, then refetch events. */
function applyUpdate(o: Order) {
  if (order.value) order.value = { ...order.value, ...o }
  emit('updated', o)
  if (props.orderId) void load(props.orderId)
}

// ---- change price ----
const priceDlg = ref(false)
const price = reactive({ amount: '', reason: '', busy: false, error: '' })
function openPrice() {
  price.amount = order.value?.amount ?? ''
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
    applyUpdate(await api.orders.changePrice(o.id, normalizeAmount(price.amount)!, price.reason.trim()))
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
async function uploadAttachment(opt: UploadRequestOptions) {
  deliver.uploading = true
  try {
    const c = await compressImage(opt.file)
    deliver.attachment = await api.upload(c.blob, { kind: 'image', filename: `delivery.${imageExtension(c.blob.type || opt.file.type)}` })
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

// ---- cancel ----
const cancelDlg = ref(false)
const cancel = reactive({ reason: '', busy: false, error: '' })
function openCancel() {
  cancel.reason = ''
  cancel.error = ''
  cancelDlg.value = true
}
async function submitCancel() {
  const o = order.value
  if (!o || cancel.busy) return
  if (!cancel.reason.trim()) {
    cancel.error = t('orders.reasonRequired')
    return
  }
  cancel.busy = true
  try {
    applyUpdate(await api.orders.cancel(o.id, cancel.reason.trim()))
    toastOk(t('orders.cancelled'))
    cancelDlg.value = false
  } catch (e) {
    toastError(e)
  } finally {
    cancel.busy = false
  }
}

const canChangePrice = computed(() => order.value?.status === 'pending_payment')
const canCancel = computed(() => order.value?.status === 'pending_payment')
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
          <el-button v-if="canCancel" type="danger" plain @click="openCancel">{{ t('orders.cancel') }}</el-button>
        </div>

        <el-descriptions :column="1" border size="small" class="desc">
          <el-descriptions-item :label="t('orders.col.title')">{{ order.title }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.col.buyer')">{{ order.buyer.display_name }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.buyerEmail')">{{ order.buyer.email }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.qty')">{{ order.qty }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.col.mode')">{{ t(`deliveryMode.${order.delivery_mode}`) }}</el-descriptions-item>
          <el-descriptions-item :label="t('orders.info.channel')">{{ order.channel ?? '—' }}</el-descriptions-item>
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
        <el-form-item :label="t('orders.reason')">
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
          <el-image v-if="deliver.attachment" :src="safeAssetUrl(deliver.attachment.url) ?? undefined" fit="cover" class="att" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deliverDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="success" :loading="deliver.busy" @click="submitDeliver">{{ t('orders.deliver') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cancelDlg" :title="t('orders.cancel')" width="min(440px, 94vw)" append-to-body>
      <el-form label-position="top" @submit.prevent="submitCancel">
        <el-form-item :label="t('orders.cancelReason')" :error="cancel.error">
          <el-input v-model="cancel.reason" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelDlg = false">{{ t('common.close') }}</el-button>
        <el-button type="danger" :loading="cancel.busy" @click="submitCancel">{{ t('orders.cancel') }}</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.drawer {
  min-height: 200px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
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
.att {
  width: 96px;
  height: 96px;
  border-radius: 8px;
  margin-top: 8px;
}
</style>
