<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { copyText, formatMoney, goBack, isApiError, type UsdtPayView } from '@24goods/shared'
import { api } from '../api'
import { payCheckout, payOrder } from '../lib/orderActions'
import { showSuccessToast, toastError } from '../ui'

/**
 * Our own USDT pay page (review-v5.2 §7): network, receiving address, exact USDT amount,
 * countdown and a QR of the address. It only DISPLAYS and polls the status; the order is
 * fulfilled solely by the server-side notify pipeline.
 */
const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const no = computed(() => String(route.params.no ?? ''))
const isCheckout = computed(() => no.value.toUpperCase().startsWith('C'))
const view = ref<UsdtPayView | null>(null)
const failed = ref(false)
const now = ref(Date.now())
const fetchedAt = ref(Date.now())
const PAID = new Set(['paid', 'delivered', 'completed'])

async function load(silent = false) {
  try {
    view.value = await api.usdtPay(no.value)
    fetchedAt.value = Date.now()
    failed.value = false
  } catch (e) {
    if (!silent) {
      failed.value = true
      if (!(isApiError(e) && e.status === 404)) toastError(e)
    }
  }
}
void load()

const left = computed(() => {
  const v = view.value
  if (!v) return 0
  return Math.max(0, v.seconds_left - Math.floor((now.value - fetchedAt.value) / 1000))
})
const clock = computed(() => `${String(Math.floor(left.value / 60)).padStart(2, '0')}:${String(left.value % 60).padStart(2, '0')}`)
const paid = computed(() => !!view.value && PAID.has(view.value.status))
const review = computed(() => view.value?.status === 'manual_review')
const expired = computed(() => !!view.value && !paid.value && !review.value && (view.value.expired || left.value === 0))
const qr = computed(() => (view.value ? `data:image/svg+xml;base64,${btoa(view.value.qr_svg)}` : ''))

const timer = setInterval(() => {
  now.value = Date.now()
}, 1000)
const poll = setInterval(() => {
  if (!paid.value && !review.value && !document.hidden) void load(true)
}, 4000)
onBeforeUnmount(() => {
  clearInterval(timer)
  clearInterval(poll)
})

async function copy(text: string) {
  if (await copyText(text)) showSuccessToast(t('pay.copied'))
}
function orderLink() {
  void router.push(isCheckout.value ? '/orders' : `/orders/${encodeURIComponent(no.value)}`)
}
async function retry() {
  try {
    if (isCheckout.value) await payCheckout(no.value)
    else await payOrder({ id: no.value, checkout_no: null })
  } catch {
    /* already toasted */
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('pay.usdtTitle')" left-arrow fixed placeholder safe-area-inset-top @click-left="goBack(router, '/orders')" />
    <div v-if="failed" class="pad center">
      <p class="muted">{{ t('pay.loadFail') }}</p>
      <van-button round block @click="orderLink">{{ t('pay.viewOrder') }}</van-button>
    </div>
    <div v-else-if="!view" class="pad center"><van-loading /></div>
    <div v-else class="pad">
      <div class="card box">
        <p class="net">{{ view.label }} · {{ view.network }}</p>
        <template v-if="paid || review">
          <p class="state ok">{{ paid ? t('pay.confirmed') : t('pay.review') }}</p>
          <van-button type="primary" round block @click="orderLink">{{ t('pay.viewOrder') }}</van-button>
        </template>
        <template v-else-if="expired">
          <p class="state warn">{{ t('pay.expired') }}</p>
          <van-button type="primary" round block @click="retry">{{ t('pay.retry') }}</van-button>
        </template>
        <template v-else>
          <img class="qr" :src="qr" :alt="t('pay.qrAlt')" width="180" height="180" />
          <div class="row">
            <span class="muted">{{ t('pay.amount') }}</span>
            <button type="button" class="val big" @click="copy(view.usdt_amount)">{{ view.usdt_amount }} USDT</button>
          </div>
          <div class="row col">
            <span class="muted">{{ t('pay.address') }}</span>
            <button type="button" class="val addr" @click="copy(view.address)">{{ view.address }}</button>
          </div>
          <div class="row">
            <span class="muted">{{ t('pay.expiresIn') }}</span>
            <span class="val">{{ clock }}</span>
          </div>
          <div class="row">
            <span class="muted">{{ t('order.amount') }}</span>
            <span class="val">{{ formatMoney(String(view.fiat_amount_minor / 100), view.currency, locale) }}</span>
          </div>
          <p class="state wait"><van-loading size="16" /> {{ t('pay.waiting') }}</p>
          <p class="muted small">{{ t('pay.exact') }}</p>
          <van-button round block @click="copy(view.address)">{{ t('pay.copy') }}</van-button>
        </template>
      </div>
    </div>
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
.center {
  text-align: center;
}
.box {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: stretch;
}
.net {
  margin: 0;
  font-weight: 700;
  text-align: center;
}
.qr {
  align-self: center;
  background: #fff;
  padding: 6px;
  border-radius: 8px;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}
.row.col {
  flex-direction: column;
  align-items: stretch;
}
.val {
  background: none;
  border: 0;
  padding: 0;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-align: right;
  cursor: pointer;
}
.val.big {
  font-size: 20px;
}
.val.addr {
  text-align: left;
  word-break: break-all;
  min-height: 44px;
}
.state {
  margin: 0;
  text-align: center;
}
.state.ok {
  color: var(--van-success-color, #07c160);
}
.state.warn {
  color: var(--van-danger-color, #ee0a24);
}
</style>
