<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { formatDateTime, goBack, type ResellerOrder } from '@24goods/shared'
import { api } from '../../api'
import { minorMoney } from '../../stores/reseller'
import { toastError } from '../../ui'

const { t, locale } = useI18n()
const router = useRouter()
const items = ref<ResellerOrder[]>([])
const page = ref(1)
const loading = ref(false)
const finished = ref(false)
const error = ref(false)
const PAGE = 20
async function load() {
  try {
    const r = await api.reseller.orders({ page: page.value, page_size: PAGE })
    items.value.push(...r.items)
    finished.value = items.value.length >= r.total || r.items.length < PAGE
    page.value++
  } catch (e) {
    error.value = true
    toastError(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.orders')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/reseller')" />
    <van-list v-model:loading="loading" v-model:error="error" :finished="finished" :finished-text="items.length ? t('common.noMore') : t('reseller.noOrders')" :error-text="t('home.loadFailed')" @load="load">
      <div v-for="o in items" :key="o.order_no" class="card row">
        <div class="top"><span class="title">{{ o.title }}</span><span class="muted st">{{ t(`orderStatus.${o.status}`) }}</span></div>
        <div class="muted small">{{ o.order_no }} · ×{{ o.qty }} · {{ formatDateTime(o.created_at, locale) }}<template v-if="o.buyer"> · {{ o.buyer }}</template></div>
        <div class="nums">
          <span>{{ minorMoney(o.amount_minor, o.currency, locale) }}</span>
          <span class="earn">{{ t('reseller.earned') }} {{ minorMoney(o.markup_minor, o.currency, locale) }}</span>
        </div>
      </div>
    </van-list>
  </div>
</template>

<style scoped>
.row {
  margin: 8px 12px;
  padding: 12px;
}
.top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.title {
  font-weight: 600;
  font-size: 14px;
  overflow-wrap: anywhere;
}
.st {
  flex: none;
  font-size: 12px;
}
.small {
  font-size: 12px;
  margin-top: 2px;
  overflow-wrap: anywhere;
}
.nums {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
}
.earn {
  color: var(--c-success, #07a35a);
}
</style>
