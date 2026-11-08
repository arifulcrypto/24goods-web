<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatMoney, safePayUrl, type EpayPaymentView } from '@24goods/shared'
import { api } from '../api'
import { toastError } from '../ui'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const view = ref<EpayPaymentView | null>(null)
const failed = ref(false)
const busy = ref(false)
const qr = computed(() => view.value?.qr_svg ? `data:image/svg+xml;base64,${btoa(view.value.qr_svg)}` : '')
const done = computed(() => !!view.value && ['paid', 'delivered', 'completed'].includes(view.value.status))
const redirect = computed(() => view.value?.redirect_url ? safePayUrl(view.value.redirect_url, undefined, view.value.redirect_host ? [view.value.redirect_host] : []) : null)
async function load() {
  if (busy.value) return
  busy.value = true
  try {
    view.value = await api.epayPay(String(route.params.no))
    failed.value = false
  } catch (e) {
    failed.value = true
    toastError(e)
  } finally { busy.value = false }
}
onMounted(() => void load())
const timer = setInterval(() => {
  if (!document.hidden && !done.value) void load()
}, 10000)
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('epay.title')" left-arrow @click-left="router.push('/orders')" />
    <section class="card pad pay-card">
      <van-loading v-if="busy && !view" />
      <template v-if="view">
        <h2>{{ formatMoney(view.amount, view.currency, locale) }}</h2>
        <p v-if="done">{{ t('pay.confirmed') }}</p>
        <p v-else-if="view.status === 'manual_review'">{{ t('pay.review') }}</p>
        <p v-else-if="view.expired">{{ t('pay.expired') }}</p>
        <template v-else>
          <img v-if="qr" :src="qr" :alt="t('pay.qrAlt')" width="240" height="240" />
          <p>{{ t('epay.checkPayee') }}</p>
          <a v-if="redirect" :href="redirect" class="wallet-link" rel="noreferrer">{{ t('epay.openCashier') }}</a>
          <p v-if="view.attempt_status === 'unknown'">{{ t('epay.unknown') }}</p>
          <p v-if="view.expires_at" class="muted">{{ t('epay.expires', { at: new Date(view.expires_at).toLocaleString(locale) }) }}</p>
        </template>
      </template>
      <p v-if="failed">{{ t('pay.loadFail') }}</p>
      <van-button round block :loading="busy" @click="load">{{ t('epay.refresh') }}</van-button>
      <van-button round block plain @click="router.push('/orders')">{{ t('pay.viewOrder') }}</van-button>
    </section>
  </div>
</template>

<style scoped>
.pay-card { margin: 16px; text-align: center; }
.wallet-link { display: block; padding: 14px; min-height: 44px; }
.pay-card img { max-width: 100%; height: auto; }
.pay-card .van-button { margin-top: 12px; }
</style>
