<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'
import { formatDateTime, formatMoney, visibleEmail, type Order, type OrderStatus } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminOrderV5 } from '../api-v5'
import type { AdminReseller } from '../api-v5'
import CloseOrderDialog from '../components/CloseOrderDialog.vue'
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

type AdminOrder = AdminOrderV5
const resellerId = ref(typeof route.query.reseller === 'string' ? route.query.reseller : '')
const overdue = ref(route.query.overdue === '1')
// display order = payment priority (#58): Alipay, then USDT X Layer -> Arbitrum One -> TRC20
const CHANNELS = ['zpay_alipay', 'usdt_xlayer', 'usdt_arbitrum', 'usdt_trc20'] as const
const channel = ref(typeof route.query.channel === 'string' ? route.query.channel : '')
function channelLabel(c: string | null | undefined): string {
  return c && (CHANNELS as readonly string[]).includes(c) ? t(`payChannel.${c}`) : c || '—'
}
const resellers = ref<AdminReseller[]>([])
const table = usePaged<AdminOrder>((page, page_size) =>
  apiV5.orders.list({ status: status.value, q: q.value.trim() || undefined, reseller_id: resellerId.value, overdue_manual: overdue.value || undefined, channel: channel.value || undefined, page, page_size }),
)
watch([resellerId, overdue, channel], ([r, o, c]) => {
  void router.replace({ query: { ...route.query, reseller: r || undefined, overdue: o ? '1' : undefined, channel: c || undefined } })
  void table.reload()
})
let debounce: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void table.reload(), 300)
})
watch(status, (s) => {
  void router.replace({ query: { ...route.query, status: s || undefined } })
  void table.reload()
})
onMounted(async () => {
  void table.load()
  try {
    resellers.value = (await apiV5.resellers.list({ page: 1, page_size: 100 })).items
  } catch {
    /* staff (non-admin) cannot list resellers: filter hidden */
  }
})
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

const closing = ref<AdminOrder | null>(null)
const closeDlg = ref(false)
function startClose(row: AdminOrder) {
  closing.value = row
  closeDlg.value = true
}
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
      <div class="toolbar">
        <el-select v-if="resellers.length" v-model="resellerId" clearable style="width: 180px" :placeholder="t('v5.orders.storeFilter')">
          <el-option v-for="r in resellers" :key="r.id" :value="r.id" :label="r.store_name" />
        </el-select>
        <el-select v-model="channel" clearable style="width: 170px" :placeholder="t('payChannel.filter')">
          <el-option v-for="c in CHANNELS" :key="c" :value="c" :label="t(`payChannel.${c}`)" />
          <el-option value="none" :label="t('payChannel.none')" />
        </el-select>
        <el-checkbox v-model="overdue" border>{{ t('v5.orders.overdueFilter') }}</el-checkbox>
        <el-input v-model="q" :placeholder="t('orders.search')" :prefix-icon="Search" clearable style="width: 280px" />
      </div>
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
            <div class="muted small">{{ visibleEmail(row.buyer.email) }}</div>
            <router-link v-if="row.buyer.email" class="small" :to="{ name: 'users', query: { q: row.buyer.email } }" @click.stop>{{ t('v5.users.manage') }}</router-link>
          </template>
        </el-table-column>
        <el-table-column :label="t('orders.col.amount')" width="130">
          <template #default="{ row }"><span class="price">{{ formatMoney(row.amount, row.currency, locale) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('payChannel.column')" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.channel" size="small" :type="String(row.channel).startsWith('usdt_') ? 'success' : 'primary'" effect="plain">{{ channelLabel(row.channel) }}</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('orders.col.status')" width="130">
          <template #default="{ row }"><OrderStatusTag :status="row.status" /></template>
        </el-table-column>
        <el-table-column :label="t('orders.col.mode')" width="130">
          <template #default="{ row }">
            {{ (row.delivery_mode as string) === 'system' ? t('v5.orders.system') : t(`deliveryMode.${row.delivery_mode}`) }}
            <el-tag v-if="row.overdue_manual" size="small" type="danger" class="tag">{{ t('v5.orders.overdue') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('v5.orders.store')" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.kind === 'reseller_signup'" size="small" type="warning">{{ t('v5.orders.kindSignup') }}</el-tag>
            <span v-else-if="row.reseller">{{ row.reseller.store_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('orders.col.created')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="120" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending_payment'" link type="danger" @click.stop="startClose(row as AdminOrder)">{{ t('orders.close') }}</el-button>
          </template>
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
    <CloseOrderDialog
      v-model="closeDlg"
      :order-id="closing?.id ?? null"
      :checkout-no="(closing as (AdminOrder & { checkout_no?: string | null }) | null)?.checkout_no"
      @closed="merge"
    />
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
.tag {
  margin-left: 4px;
}
.clickable :deep(.el-table__row) {
  cursor: pointer;
}
</style>
