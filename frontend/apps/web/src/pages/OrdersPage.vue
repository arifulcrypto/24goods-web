<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { canRequestAfterSales, formatDateTime, formatMoney, type AfterSales, type Order } from '@24goods/shared'
import AfterSalesSheet from '../components/AfterSalesSheet.vue'
import { api } from '../api'
import { useRealtimeStore } from '../stores/realtime'

const { t, locale } = useI18n()
const router = useRouter()
const rt = useRealtimeStore()
const items = ref<Order[]>([])
const loading = ref(false)
const finished = ref(false)
const error = ref(false)
const refreshing = ref(false)
let page = 0
const asFor = ref<string | null>(null)
const asSheet = ref(false)
function openAfterSales(id: string) {
  asFor.value = id
  asSheet.value = true
}
function ticketCreated(tk: AfterSales) {
  const i = items.value.findIndex((x) => x.id === tk.order_id)
  if (i >= 0) items.value.splice(i, 1, { ...items.value[i]!, after_sales: tk })
}

async function load() {
  loading.value = true
  error.value = false
  try {
    const r = await api.orders.list({ page: page + 1, page_size: 20 })
    page = r.page || page + 1
    const seen = new Set(items.value.map((o) => o.id))
    items.value = [...items.value, ...r.items.filter((o) => !seen.has(o.id))]
    finished.value = r.items.length === 0 || items.value.length >= r.total
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}
async function refresh() {
  page = 0
  items.value = []
  finished.value = false
  await load()
  refreshing.value = false
}

const off = rt.on('order', (o) => {
  const i = items.value.findIndex((x) => x.id === o.id)
  if (i >= 0) items.value.splice(i, 1, o)
  else items.value.unshift(o)
})
onBeforeUnmount(off)

const tone = (s: Order['status']) =>
  s === 'pending_payment' ? 'warn' : s === 'paid' || s === 'manual_review' ? 'info' : s === 'delivered' || s === 'completed' ? 'ok' : 'off'
</script>

<template>
  <div class="page with-tabbar">
    <van-nav-bar :title="t('orders.title')" class="dock-top" fixed placeholder safe-area-inset-top :right-text="t('lookup.title')" @click-right="router.push('/lookup')" />
    <van-pull-refresh v-model="refreshing" @refresh="refresh">
      <van-list
        v-model:loading="loading"
        v-model:error="error"
        :finished="finished"
        :finished-text="items.length ? t('common.noMore') : ''"
        :error-text="t('home.loadFailed')"
        @load="load"
      >
        <div class="orders">
          <div v-for="o in items" :key="o.id" class="card ocard">
            <RouterLink :to="`/orders/${encodeURIComponent(o.id)}`" class="olink">
            <div class="ocard-top">
              <span class="muted small">{{ t('orders.orderNo', { no: o.order_no }) }}</span>
              <span class="status" :class="tone(o.status)">{{ t(`orderStatus.${o.status}`) }}</span>
            </div>
            <div class="ocard-title">{{ o.title }}</div>
            <div class="ocard-bottom">
              <span class="muted small">{{ formatDateTime(o.created_at, locale) }} · {{ t('orders.qtyN', { n: o.qty }) }}</span>
              <span class="price">{{ formatMoney(o.amount, o.currency, locale) }}</span>
            </div>
          </RouterLink>
            <div v-if="['delivered', 'completed'].includes(o.status)" class="oas">
              <button v-if="canRequestAfterSales(o)" type="button" class="oas-btn" @click="openAfterSales(o.id)">{{ t('afterSales.request') }}</button>
              <span v-else-if="o.after_sales" class="muted small">{{ t('afterSales.ticket') }}: {{ t(`afterSales.status.${o.after_sales.status}`) }}</span>
              <span v-else class="muted small">{{ t('afterSales.expiredShort') }}</span>
            </div>
          </div>
          <template v-if="loading && !items.length">
            <div v-for="i in 3" :key="`s${i}`" class="card ocard">
              <div class="skeleton" style="height: 14px; width: 50%" />
              <div class="skeleton" style="height: 18px; margin-top: 10px" />
              <div class="skeleton" style="height: 14px; width: 30%; margin-top: 10px" />
            </div>
          </template>
        </div>
        <van-empty v-if="finished && !items.length" image="default" :description="t('orders.empty')">
          <van-button round type="primary" to="/">{{ t('orders.goShopping') }}</van-button>
        </van-empty>
      </van-list>
    </van-pull-refresh>
    <AfterSalesSheet v-if="asFor" v-model:show="asSheet" :order-id="asFor" @created="ticketCreated" />
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
}
.orders {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}
.ocard {
  padding: 12px 14px;
}
.olink {
  display: block;
  text-decoration: none;
  color: inherit;
}
.oas {
  border-top: 1px solid var(--c-line);
  margin-top: 8px;
  padding-top: 4px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  min-height: 44px;
}
.oas-btn {
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--c-line);
  border-radius: 999px;
  background: var(--c-surface);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.ocard-top,
.ocard-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.ocard-title {
  font-weight: 600;
  margin: 6px 0;
  overflow-wrap: anywhere;
}
.small {
  font-size: 12px;
}
.status {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.status.warn {
  color: var(--c-price);
}
.status.info {
  color: #3b4fb8;
}
.status.ok {
  color: var(--c-success);
}
.status.off {
  color: var(--c-muted);
}
</style>
