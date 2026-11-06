<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { amountToMinor, formatDateTime, goBack, type ResellerPayout } from '@24goods/shared'
import { api } from '../../api'
import { minorMoney, useResellerStore } from '../../stores/reseller'
import { showSuccessToast, toastError } from '../../ui'

const { t, locale } = useI18n()
const router = useRouter()
const store = useResellerStore()
const list = ref<ResellerPayout[]>([])
const amount = ref('')
const busy = ref(false)
const me = computed(() => store.me)
const minor = computed(() => amountToMinor(amount.value))
const valid = computed(() => minor.value !== null && minor.value > 0 && !!me.value && minor.value <= me.value.balances.available_minor)
const hasAccount = computed(() => !!me.value?.payout_account?.account)
const open = computed(() => list.value.some((p) => p.status === 'requested'))

async function refresh() {
  const r = await api.reseller.payouts()
  list.value = Array.isArray(r) ? r : r.items
}
onMounted(async () => {
  try {
    await store.load(true)
    await refresh()
  } catch (e) {
    toastError(e)
  }
})
async function request() {
  if (!valid.value || busy.value || minor.value === null) return
  busy.value = true
  try {
    await api.reseller.requestPayout(minor.value)
    amount.value = ''
    showSuccessToast(t('reseller.payoutRequested'))
    await Promise.all([store.load(true), refresh()])
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.payouts')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/reseller')" />
    <section v-if="me" class="section card pad">
      <div class="muted small">{{ t('reseller.balAvailable') }}</div>
      <div class="price big">{{ minorMoney(me.balances.available_minor, 'CNY', locale) }}</div>
      <p v-if="!hasAccount" class="warn">
        {{ t('reseller.needAccount') }}
        <RouterLink to="/reseller/settings">{{ t('reseller.settings') }}</RouterLink>
      </p>
      <van-field v-model="amount" inputmode="decimal" :label="t('reseller.amount')" placeholder="0.00" :disabled="!hasAccount || open" />
      <p v-if="open" class="muted small">{{ t('reseller.payoutOpen') }}</p>
      <van-button block round type="primary" :loading="busy" :disabled="!valid || !hasAccount || open" @click="request">{{ t('reseller.requestPayout') }}</van-button>
    </section>
    <h3 class="hist">{{ t('reseller.history') }}</h3>
    <div v-if="!list.length" class="muted empty">{{ t('reseller.noPayouts') }}</div>
    <div v-for="p in list" :key="p.id" class="card row">
      <div class="top"><strong>{{ minorMoney(p.amount_minor, p.currency, locale) }}</strong><span class="muted">{{ t(`reseller.payoutStatus.${p.status}`) }}</span></div>
      <div class="muted small">{{ formatDateTime(p.created_at, locale) }}<template v-if="p.note"> · {{ p.note }}</template></div>
    </div>
  </div>
</template>

<style scoped>
.pad {
  padding: 14px 16px;
}
.big {
  font-size: 24px;
  margin-bottom: 8px;
}
.warn {
  font-size: 13px;
  color: var(--c-danger, #c0392b);
}
.hist {
  margin: 16px 16px 4px;
  font-size: 15px;
}
.empty {
  padding: 12px 16px;
}
.row {
  margin: 8px 12px;
  padding: 12px;
}
.top {
  display: flex;
  justify-content: space-between;
}
.small {
  font-size: 12px;
}
</style>
