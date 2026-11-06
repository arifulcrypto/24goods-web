<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatMoney, type AdminStats } from '@24goods/shared'
import { api, apiV3 } from '../api'
import { useRealtimeStore } from '../stores/realtime'
import { toastError } from '../ui'

const { t, locale } = useI18n()
const rt = useRealtimeStore()
const stats = ref<AdminStats | null>(null)
const loading = ref(true)
/** null = unknown (endpoint failed); counted from the existing after-sales list (status=open). */
const afterSalesOpen = ref<number | null>(null)

async function load() {
  try {
    stats.value = await api.stats()
    try {
      afterSalesOpen.value = (await apiV3.afterSales.list({ status: 'open', page: 1, page_size: 1 })).total
    } catch {
      afterSalesOpen.value = null
    }
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
let timer: ReturnType<typeof setTimeout> | null = null
function scheduleRefresh() {
  if (timer) return
  timer = setTimeout(() => {
    timer = null
    void load()
  }, 3000)
}
const offs = [rt.on('order', scheduleRefresh), rt.on('conversation', scheduleRefresh)]
onMounted(load)
onBeforeUnmount(() => {
  offs.forEach((o) => o())
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="page">
    <div class="cards">
      <el-card v-for="k in ['orders_today', 'revenue_today', 'pending_payment', 'awaiting_manual', 'open_conversations'] as const" :key="k" shadow="never" class="stat">
        <el-skeleton v-if="loading" :rows="1" animated />
        <template v-else-if="stats">
          <div class="stat-label muted">
            {{
              t(
                {
                  orders_today: 'dashboard.ordersToday',
                  revenue_today: 'dashboard.revenueToday',
                  pending_payment: 'dashboard.pendingPayment',
                  awaiting_manual: 'dashboard.awaitingManual',
                  open_conversations: 'dashboard.openConversations',
                }[k],
              )
            }}
          </div>
          <div class="stat-value" :class="{ price: k === 'revenue_today' }">
            {{ k === 'revenue_today' ? formatMoney(stats.revenue_today, 'CNY', locale) : stats[k] }}
          </div>
          <RouterLink v-if="k === 'open_conversations'" to="/inbox" class="stat-link">{{ t('dashboard.goInbox') }} →</RouterLink>
          <RouterLink
            v-else-if="k === 'pending_payment' || k === 'awaiting_manual'"
            :to="{ path: '/orders', query: { status: k === 'pending_payment' ? 'pending_payment' : 'paid' } }"
            class="stat-link"
          >
            {{ t('dashboard.goOrders') }} →
          </RouterLink>
        </template>
      </el-card>
      <el-card shadow="never" class="stat">
        <el-skeleton v-if="loading" :rows="1" animated />
        <template v-else>
          <div class="stat-label muted">{{ t('dashboard.afterSales') }}</div>
          <div class="stat-value">{{ afterSalesOpen ?? '—' }}</div>
          <RouterLink to="/after-sales" class="stat-link">{{ t('dashboard.goOrders') }} →</RouterLink>
        </template>
      </el-card>
    </div>

    <div class="panel">
      <h2 class="h2">{{ t('dashboard.lowStock') }}</h2>
      <el-table v-loading="loading" :data="stats?.low_stock ?? []" :empty-text="t('dashboard.noLowStock')">
        <el-table-column :label="t('dashboard.product')" min-width="240">
          <template #default="{ row }">
            <div class="prod">
              <el-image v-if="row.cover" :src="row.cover" fit="cover" class="thumb" lazy />
              <span>{{ row.title }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.stock')" width="120">
          <template #default="{ row }">
            <el-tag :type="row.stock === 0 ? 'danger' : 'warning'">{{ row.stock ?? '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.price')" width="140">
          <template #default="{ row }"><span class="price">{{ formatMoney(row.price, row.currency, locale) }}</span></template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.stat {
  border-radius: 10px;
}
.stat-label {
  font-size: 13px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}
.stat-link {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-primary);
  text-decoration: none;
}
.h2 {
  font-size: 16px;
  margin: 0 0 12px;
}
.prod {
  display: flex;
  align-items: center;
  gap: 10px;
}
.thumb {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  flex: none;
}
</style>
