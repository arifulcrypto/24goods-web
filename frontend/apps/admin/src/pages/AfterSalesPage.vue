<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ChatLineRound } from '@element-plus/icons-vue'
import { formatDateTime, formatMoney, safeAssetUrl, visibleEmail } from '@24goods/shared'
import { apiV3 } from '../api'
import type { AfterSalesTicket } from '../api-v3'
import { usePaged } from '../composables/usePaged'
import { TICKET_STATUSES, availableActions, needsConfirm, statusTagType, validateAction, type TicketAction, type TicketStatus } from '../lib/afterSales'
import { useAuthStore } from '../stores/auth'
import { useDeskStore } from '../stores/desk'
import { useRealtimeStore } from '../stores/realtime'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const rt = useRealtimeStore()
const desk = useDeskStore()
const auth = useAuthStore()
const status = ref<TicketStatus | ''>((TICKET_STATUSES as readonly string[]).includes(route.query.status as string) ? (route.query.status as TicketStatus) : '')
const openId = ref<string | null>(typeof route.query.open === 'string' ? route.query.open : null)
const ticket = ref<AfterSalesTicket | null>(null)
const loadingTicket = ref(false)
const busy = ref<TicketAction | null>(null)

const table = usePaged<AfterSalesTicket>((page, page_size) => apiV3.afterSales.list({ status: status.value, page, page_size }))
watch(status, () => void table.reload())
onMounted(() => void table.load())
const off = rt.on('after_sales', (tk) => {
  table.patch((x) => x.id === tk.id, { ...(table.items.value.find((x) => x.id === tk.id) as AfterSalesTicket), ...tk })
  if (!table.items.value.some((x) => x.id === tk.id)) void table.reload()
  if (openId.value === tk.id) void loadTicket(tk.id)
})
onBeforeUnmount(off)

async function loadTicket(id: string) {
  loadingTicket.value = true
  try {
    ticket.value = await apiV3.afterSales.get(id)
  } catch (e) {
    toastError(e)
  } finally {
    loadingTicket.value = false
  }
}
watch(
  openId,
  (id) => {
    ticket.value = null
    if (id) void loadTicket(id)
  },
  { immediate: true },
)
watch(
  () => route.query.open,
  (v) => {
    if (typeof v === 'string') openId.value = v
  },
)
function open(row: AfterSalesTicket) {
  openId.value = row.id
}
function close() {
  openId.value = null
  if (route.query.open) void router.replace({ query: { ...route.query, open: undefined } })
}

const actions = computed(() => (ticket.value ? availableActions(ticket.value.status, auth.isAdmin ? 'admin' : 'agent') : []))
const orderNo = (x: AfterSalesTicket) => x.order_no ?? x.order?.order_no ?? '—'
const deadline = computed(() => ticket.value?.after_sales_until ?? ticket.value?.order?.after_sales_until ?? null)
const images = computed(() => (ticket.value?.attachments ?? []).map((a) => safeAssetUrl(a.url)).filter((u): u is string => !!u))
const mode = computed(() => ticket.value?.order?.delivery_mode ?? 'manual')

// ---- action dialog (note / delivery text) ----
const dlg = reactive({ open: false, action: 'reject' as TicketAction, note: '', text: '', error: '' })
function startAction(a: TicketAction) {
  if (a === 'processing') return void run(a, {})
  dlg.action = a
  dlg.note = ''
  dlg.text = ''
  dlg.error = ''
  dlg.open = true
}
async function submitDialog() {
  const err = validateAction(dlg.action, { note: dlg.note, delivery_text: dlg.text }, mode.value)
  if (err) {
    dlg.error = t(err)
    return
  }
  if (needsConfirm(dlg.action)) {
    const msg = dlg.action === 'replace' && mode.value === 'auto_card' ? t('afterSales.confirm.replaceAuto') : t(`afterSales.confirm.${dlg.action}`)
    if (!(await confirmBox(msg, t(`afterSales.action.${dlg.action}`)))) return
  }
  await run(dlg.action, { note: dlg.note.trim() || undefined, delivery_text: dlg.action === 'replace' && mode.value === 'manual' ? dlg.text.trim() : undefined })
}
async function run(a: TicketAction, body: { note?: string; delivery_text?: string }) {
  const tk = ticket.value
  if (!tk || busy.value) return
  busy.value = a
  try {
    ticket.value = { ...tk, ...(await apiV3.afterSales.action(tk.id, { action: a, ...body })) }
    table.patch((x) => x.id === tk.id, { ...tk, ...ticket.value })
    toastOk(t('afterSales.done'))
    dlg.open = false
    void desk.refreshAfterSales()
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = null
  }
}
function tagType(a: TicketAction) {
  return a === 'refund' || a === 'reject' ? 'danger' : a === 'replace' ? 'primary' : a === 'resolve' ? 'success' : 'default'
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <el-radio-group v-model="status" class="status-tabs">
        <el-radio-button value="">{{ t('orders.allStatus') }}</el-radio-button>
        <el-radio-button v-for="s in TICKET_STATUSES" :key="s" :value="s">{{ t(`afterSales.status.${s}`) }}</el-radio-button>
      </el-radio-group>
    </div>
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')" @row-click="open">
        <el-table-column :label="t('orders.col.orderNo')" min-width="150">
          <template #default="{ row }">
            <el-button link type="primary" class="mono" @click.stop="open(row as AfterSalesTicket)">{{ orderNo(row as AfterSalesTicket) }}</el-button>
          </template>
        </el-table-column>
        <el-table-column :label="t('orders.col.buyer')" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.buyer?.display_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('afterSales.reasonLabel')" min-width="160">
          <template #default="{ row }">{{ t(`afterSales.reason.${row.reason}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="130">
          <template #default="{ row }"><el-tag size="small" :type="statusTagType(row.status)">{{ t(`afterSales.status.${row.status}`) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('common.createdAt')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
      </div>
    </div>

    <el-drawer :model-value="!!openId" :title="ticket ? t('afterSales.detail', { no: orderNo(ticket) }) : ''" size="min(560px, 100vw)" @close="close">
      <div v-loading="loadingTicket" class="drawer">
        <template v-if="ticket">
          <div class="head">
            <el-tag :type="statusTagType(ticket.status)">{{ t(`afterSales.status.${ticket.status}`) }}</el-tag>
            <RouterLink v-if="ticket.conversation_id" :to="{ path: '/inbox', query: { c: ticket.conversation_id } }">
              <el-button :icon="ChatLineRound">{{ t('afterSales.openConversation') }}</el-button>
            </RouterLink>
          </div>
          <div v-if="actions.length" class="actions" role="group" :aria-label="t('common.actions')">
            <el-button v-for="a in actions" :key="a" :type="tagType(a)" :plain="a !== 'replace'" :loading="busy === a" :disabled="!!busy && busy !== a" @click="startAction(a)">
              {{ t(`afterSales.action.${a}`) }}
            </el-button>
          </div>
          <el-descriptions :column="1" border size="small" class="desc">
            <el-descriptions-item :label="t('orders.col.orderNo')">
              <RouterLink :to="{ path: '/orders', query: { open: ticket.order_id } }" class="mono">{{ orderNo(ticket) }}</RouterLink>
            </el-descriptions-item>
            <el-descriptions-item v-if="ticket.order" :label="t('orders.col.title')">{{ ticket.order.title }} × {{ ticket.order.qty }}</el-descriptions-item>
            <el-descriptions-item v-if="ticket.order" :label="t('orders.col.amount')">{{ formatMoney(ticket.order.amount, ticket.order.currency, locale) }}</el-descriptions-item>
            <el-descriptions-item v-if="ticket.order" :label="t('orders.col.mode')">{{ t(`deliveryMode.${ticket.order.delivery_mode}`) }}</el-descriptions-item>
            <el-descriptions-item :label="t('orders.col.buyer')">{{ ticket.buyer?.display_name ?? '—' }}<span v-if="visibleEmail(ticket.buyer?.email)" class="muted"> · {{ visibleEmail(ticket.buyer?.email) }}</span></el-descriptions-item>
            <el-descriptions-item :label="t('afterSales.reasonLabel')">{{ t(`afterSales.reason.${ticket.reason}`) }}</el-descriptions-item>
            <el-descriptions-item :label="t('afterSales.description')"><p class="pre">{{ ticket.description || '—' }}</p></el-descriptions-item>
            <el-descriptions-item v-if="ticket.order?.delivered_at" :label="t('orders.info.deliveredAt')">{{ formatDateTime(ticket.order.delivered_at, locale) }}</el-descriptions-item>
            <el-descriptions-item :label="t('afterSales.deadline')">{{ deadline ? formatDateTime(deadline, locale) : '—' }}</el-descriptions-item>
            <el-descriptions-item :label="t('common.createdAt')">{{ formatDateTime(ticket.created_at, locale) }}</el-descriptions-item>
            <el-descriptions-item v-if="ticket.resolution_note" :label="t('afterSales.resolutionNote')"><p class="pre">{{ ticket.resolution_note }}</p></el-descriptions-item>
          </el-descriptions>
          <div v-if="images.length" class="imgs">
            <el-image v-for="(u, i) in images" :key="u" :src="u" :preview-src-list="images" :initial-index="i" fit="cover" class="img" preview-teleported />
          </div>
        </template>
      </div>

      <el-dialog v-model="dlg.open" :title="t(`afterSales.action.${dlg.action}`)" width="min(500px, 94vw)" append-to-body>
        <el-alert v-if="dlg.action === 'refund'" type="warning" :closable="false" show-icon :title="t('afterSales.refundNote')" class="gap" />
        <el-alert v-if="dlg.action === 'replace' && mode === 'auto_card'" type="info" :closable="false" show-icon :title="t('afterSales.confirm.replaceAuto')" class="gap" />
        <el-form label-position="top" @submit.prevent="submitDialog">
          <el-form-item v-if="dlg.action === 'replace' && mode === 'manual'" :label="t('orders.deliverText')" :error="dlg.error">
            <el-input v-model="dlg.text" type="textarea" :rows="5" maxlength="4000" show-word-limit />
          </el-form-item>
          <el-form-item :label="dlg.action === 'reject' ? t('afterSales.rejectReason') : t('afterSales.note')" :error="dlg.action === 'reject' ? dlg.error : ''">
            <el-input v-model="dlg.note" type="textarea" :rows="3" maxlength="500" show-word-limit />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dlg.open = false">{{ t('common.cancel') }}</el-button>
          <el-button :type="tagType(dlg.action)" :loading="!!busy" @click="submitDialog">{{ t('common.confirm') }}</el-button>
        </template>
      </el-dialog>
    </el-drawer>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0 16px;
}
.desc {
  margin-bottom: 16px;
}
.imgs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.img {
  width: 96px;
  height: 96px;
  border-radius: 8px;
}
.gap {
  margin-bottom: 12px;
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
