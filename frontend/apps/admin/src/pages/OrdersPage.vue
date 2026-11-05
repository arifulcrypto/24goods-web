<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'
import { formatDateTime, formatMoney, type AdminOrder, type Order, type OrderStatus } from '@24goods/shared'
import { api } from '../api'
import OrderDrawer from '../components/OrderDrawer.vue'
import OrderStatusTag from '../components/OrderStatusTag.vue'
import { usePaged } from '../composables/usePaged'
import { useRealtimeStore } from '../stores/realtime'

const STATUSES: OrderStatus[] = ['pending_payment', 'paid', 'delivered', 'completed', 'manual_review', 'cancelled', 'expired', 'refunded']
const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const rt = useRealtimeStore()
const status = ref<OrderStatus | ''>(STATUSES.includes(route.query.status as OrderStatus) ? (route.query.status as OrderStatus) : '')
const q = ref(typeof route.query.q === 'string' ? route.query.q : '')
const openId = ref<string | null>(typeof route.query.open === 'string' ? route.query.open : null)

const table = usePaged<AdminOrder>((page, page_size) =>
  api.orders.list({ status: status.value, q: q.value.trim() || undefined, page, page_size }),
)
let debounce: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void table.reload(), 300)
})
watch(status, (s) => {
  void router.replace({ query: { ...route.query, status: s || undefined } })
  void table.reload()
})
onMounted(() => void table.load())
watch(
  () => route.query.open,
  (v) => {
    if (typeof v === 'string') openId.value = v
  },
)

function merge(o: Order) {
  table.patch((x) => x.id === o.id, { ...(table.items.value.find((x) => x.id === o.id) as AdminOrder), ...o })
}
const off = rt.on('order', (o) => {
  if (table.items.value.some((x) => x.id === o.id)) merge(o)
})
onBeforeUnmount(off)

function open(row: AdminOrder) {
  openId.value = row.id
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <el-radio-group v-model="status" size="default" class="status-tabs">
        <el-radio-button value="">{{ t('orders.allStatus') }}</el-radio-button>
        <el-radio-button v-for="s in STATUSES" :key="s" :value="s">{{ t(`orderStatus.${s}`) }}</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" :placeholder="t('orders.search')" :prefix-icon="Search" clearable style="width: 280px" />
    </div>
    <div class="panel">
      <el-table
        v-loading="table.loading.value"
        :data="table.items.value"
        row-key="id"
        :empty-text="t('common.empty')"
        highlight-current-row
        class="clickable"
        @row-click="open"
      >
        <el-table-column :label="t('orders.col.orderNo')" width="190">
          <template #default="{ row }"><span class="mono">{{ row.order_no }}</span></template>
        </el-table-column>
        <el-table-column :label="t('orders.col.title')" prop="title" min-width="220" show-overflow-tooltip />
        <el-table-column :label="t('orders.col.buyer')" min-width="180">
          <template #default="{ row }">
            <div>{{ row.buyer.display_name }}</div>
            <div class="muted small">{{ row.buyer.email }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('orders.col.amount')" width="130">
          <template #default="{ row }"><span class="price">{{ formatMoney(row.amount, row.currency, locale) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('orders.col.status')" width="130">
          <template #default="{ row }"><OrderStatusTag :status="row.status" /></template>
        </el-table-column>
        <el-table-column :label="t('orders.col.mode')" width="100">
          <template #default="{ row }">{{ t(`deliveryMode.${row.delivery_mode}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('orders.col.created')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination
          layout="total, prev, pager, next"
          :total="table.total.value"
          :page-size="table.pageSize"
          :current-page="table.page.value"
          @current-change="table.load"
        />
      </div>
    </div>
    <OrderDrawer :order-id="openId" @close="openId = null" @updated="merge" />
  </div>
</template>

<style scoped>
.status-tabs {
  flex-wrap: wrap;
}
.small {
  font-size: 12px;
}
.clickable :deep(.el-table__row) {
  cursor: pointer;
}
</style>
