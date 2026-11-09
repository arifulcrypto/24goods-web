<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { copyText, goBack, type ResellerStats } from '@24goods/shared'
import { api } from '../../api'
import { minorMoney, useResellerStore } from '../../stores/reseller'
import { showToast, toastError } from '../../ui'

const { t, locale } = useI18n()
const router = useRouter()
const store = useResellerStore()
const loading = ref(true)
const stats = ref<ResellerStats | null>(null)
const me = computed(() => store.me)
const cur = 'CNY'

onMounted(async () => {
  try {
    const m = await store.load(true)
    if (!m) return void router.replace('/reseller/apply')
    if (m.status === 'active') stats.value = await api.reseller.stats()
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
})
async function copy() {
  if (me.value) showToast((await copyText(me.value.store_url)) ? t('common.copied') : t('common.copyFailed'))
}
const m = (n: number) => minorMoney(n, cur, locale.value)
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.center')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/me')" />
    <van-loading v-if="loading" class="mid" />
    <template v-else-if="me">
      <section v-if="me.status === 'pending_payment'" class="section card pad">
        <strong>{{ t('reseller.pendingTitle') }}</strong>
        <p class="muted">{{ t('reseller.pendingText') }}</p>
        <van-button v-if="me.signup_order_no" round type="primary" block :to="`/orders/${encodeURIComponent(me.signup_order_no)}`">{{ t('reseller.payNow') }}</van-button>
        <van-button v-else round plain block to="/reseller/apply">{{ t('reseller.applyAgain') }}</van-button>
      </section>
      <section v-else-if="me.status === 'suspended'" class="section card pad">
        <strong>{{ t('reseller.suspendedTitle') }}</strong>
        <p class="muted">{{ t('reseller.suspendedText') }}</p>
      </section>
      <template v-else>
        <section class="section card pad">
          <div class="muted small">{{ t('reseller.storeUrl') }}</div>
          <div class="url-row">
            <span class="url">{{ me.store_url }}</span>
            <van-button size="small" round plain class="tap" @click="copy">{{ t('common.copy') }}</van-button>
          </div>
        </section>
        <section class="section card pad grid">
          <div><div class="muted small">{{ t('reseller.balAvailable') }}</div><div class="price">{{ m(me.balances.available_minor) }}</div></div>
          <div><div class="muted small">{{ t('reseller.balPending') }}</div><div>{{ m(me.balances.pending_minor) }}</div></div>
          <div><div class="muted small">{{ t('reseller.balPaid') }}</div><div>{{ m(me.balances.paid_minor) }}</div></div>
          <div v-if="me.balances.disputed_minor"><div class="muted small">{{ t('reseller.balDisputed') }}</div><div>{{ m(me.balances.disputed_minor) }}</div></div>
        </section>
        <section v-if="stats" class="section card pad grid">
          <div><div class="muted small">{{ t('reseller.today') }}</div><div>{{ t('reseller.ordersN', { n: stats.today_orders }) }}</div><div class="muted small">{{ m(stats.today_earnings_minor) }}</div></div>
          <div><div class="muted small">{{ t('reseller.week') }}</div><div>{{ t('reseller.ordersN', { n: stats.week_orders }) }}</div><div class="muted small">{{ m(stats.week_earnings_minor) }}</div></div>
        </section>
        <section class="section card">
          <van-cell-group :border="false">
            <van-cell :title="t('reseller.products')" icon="goods-collect-o" is-link to="/reseller/products" />
            <van-cell :title="t('reseller.orders')" icon="orders-o" is-link to="/reseller/orders" />
            <van-cell :title="t('reseller.payouts')" icon="gold-coin-o" is-link to="/reseller/payouts" />
            <van-cell :title="t('reseller.settings')" icon="setting-o" is-link to="/reseller/settings" />
          </van-cell-group>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.mid {
  display: block;
  margin: 40px auto;
  text-align: center;
}
.pad {
  padding: 14px 16px;
}
.url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.url {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 600;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 12px;
}
.small {
  font-size: 12px;
}
</style>
