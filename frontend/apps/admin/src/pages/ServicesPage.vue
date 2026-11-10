<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { client } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSave } from '../composables/useSave'
import { useRead } from '../composables/useRead'
import OrderDrawer from '../components/OrderDrawer.vue'

interface Service {
  id: string
  label: string
  /** v11: standalone records have neither a buyer nor an order. */
  ref?: string
  customer_label?: string | null
  contact?: string | null
  notes?: string | null
  standalone?: boolean
  reminder_lead_days?: number | null
  buyer_id: string | null
  order_id: string | null
  owner_id: string
  status: string
  starts_at: string | null
  ends_at: string | null
  timezone: string
  version: number
  overdue: boolean
  email_reminders: boolean
  telegram_reminders: boolean
  source?: 'auto' | 'manual'
  order_no?: string | null
  product_title?: string | null
  buyer_email?: string
  days_left?: number | null
  after_sales_open?: boolean
}
interface LinkedOrder {
  kind: 'source' | 'renewal'
  order_id: string
  order_no: string
  paid_at: string | null
  amount: string
  currency: string
  status: string
  after_sales_status: string | null
}
interface ManualRenewal {
  starts_at: string | null
  ends_at: string | null
  note: string | null
  created_at: string | null
}
interface Candidate {
  order_id: string
  order_no: string
  paid_at: string | null
  amount: string
  currency: string
}
const { t, locale } = useI18n()
const auth = useAuthStore()
const { run, saving } = useSave()
const reader = useRead()
const rows = ref<Service[]>([])
const total = ref(0)
const page = ref(1)
const due = ref('')
const mine = ref(false)
const q = ref('')
const adding = ref(false)
const form = reactive({ order_no: '', label: '', buyer_id: '', order_id: '', owner_id: '' })
const editing = ref<Service | null>(null)
const linked = ref<LinkedOrder[]>([])
const manualRenewals = ref<ManualRenewal[]>([])
const candidates = ref<Candidate[]>([])
const reason = ref('')
const end = ref<Date | null>(null)
const renewal = ref('')
const drawerOrder = ref<string | null>(null)

// v11: standalone record (no payment check; order / buyer are optional links)
const recording = ref(false)
type EndMode = 'date' | 'month' | 'days'
const rec = reactive({
  label: '',
  customer_label: '',
  contact: '',
  buyer_email: '',
  order_no: '',
  notes: '',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  lead: null as number | null,
  email: false,
  telegram: false,
  endMode: 'month' as EndMode,
  days: 30,
})
const recStart = ref<Date | null>(new Date())
const recEnd = ref<Date | null>(null)
const recValid = computed(() => !!rec.label.trim() && (rec.endMode !== 'date' || (!!recEnd.value && !!recStart.value && recEnd.value > recStart.value)))
async function createRecord() {
  const body: Record<string, unknown> = {
    label: rec.label.trim(),
    timezone: rec.timezone || 'UTC',
    email_reminders: rec.email,
    telegram_reminders: rec.telegram,
    starts_at: (recStart.value ?? new Date()).toISOString(),
    ...(rec.customer_label.trim() ? { customer_label: rec.customer_label.trim() } : {}),
    ...(rec.contact.trim() ? { contact: rec.contact.trim() } : {}),
    ...(rec.buyer_email.trim() ? { buyer_email: rec.buyer_email.trim() } : {}),
    ...(rec.order_no.trim() ? { order_no: rec.order_no.trim() } : {}),
    ...(rec.notes.trim() ? { notes: rec.notes.trim() } : {}),
    ...(rec.lead ? { reminder_lead_days: rec.lead } : {}),
    ...(rec.endMode === 'date' && recEnd.value ? { ends_at: recEnd.value.toISOString() } : {}),
    ...(rec.endMode === 'days' ? { period_policy: 'fixed_days', period_days: rec.days } : {}),
  }
  const r = await run(() => client.post('/admin/services', body))
  if (r) {
    recording.value = false
    Object.assign(rec, { label: '', customer_label: '', contact: '', buyer_email: '', order_no: '', notes: '', lead: null, email: false, telegram: false })
    recStart.value = new Date()
    recEnd.value = null
    await load()
  }
}

/** Browser-local rendering of an ISO timestamp (never the raw string). */
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(locale.value) : '—')
const dueTag = (row: Service) => {
  if (row.status !== 'active' || row.days_left == null) return null
  if (row.days_left > 0) return { type: row.days_left <= 7 ? 'warning' : 'info', text: t('servicesV7.daysLeft', { n: row.days_left }) }
  return row.days_left === 0 && !row.overdue
    ? { type: 'warning', text: t('servicesV7.dueToday') }
    : { type: 'danger', text: t('servicesV7.overdueBy', { n: Math.abs(row.days_left) }) }
}

async function load() {
  const r = await reader.run(() =>
    client.get<{ items: Service[]; total: number }>('/admin/services', {
      page: page.value,
      due: due.value || undefined,
      mine: mine.value,
      q: q.value.trim() || undefined,
    }),
  )
  if (r) {
    rows.value = r.items
    total.value = r.total
  }
}
const route = useRoute()
onMounted(async () => {
  await load()
  if (typeof route.params.id === 'string') {
    const row = await reader.run(() => client.get<Service>(`/admin/services/${encodeURIComponent(String(route.params.id))}`))
    if (row) await edit(row)
  }
})
function search() {
  page.value = 1
  void load()
}
async function create() {
  const byNo = form.order_no.trim()
  const body = byNo
    ? { order_no: byNo, ...(form.label.trim() ? { label: form.label.trim() } : {}) }
    : {
        buyer_id: form.buyer_id.trim(),
        order_id: form.order_id.trim(),
        owner_id: form.owner_id.trim() || auth.user?.id,
        ...(form.label.trim() ? { label: form.label.trim() } : {}),
      }
  const r = await run(() => client.post('/admin/services', body))
  if (r) {
    adding.value = false
    Object.assign(form, { order_no: '', label: '', buyer_id: '', order_id: '', owner_id: '' })
    await load()
  }
}
async function activate(row: Service) {
  const r = await run(() => client.post(`/admin/services/${row.id}/activate`, {}))
  if (r) await load()
}
async function edit(row: Service) {
  editing.value = { ...row }
  reason.value = ''
  renewal.value = ''
  end.value = row.ends_at ? new Date(row.ends_at) : null
  linked.value = []
  manualRenewals.value = []
  candidates.value = []
  const [d, c] = await Promise.all([
    reader.run(() => client.get<{ orders: LinkedOrder[]; manual_renewals?: ManualRenewal[] }>(`/admin/services/${row.id}`)),
    row.buyer_id ? reader.run(() => client.get<{ items: Candidate[] }>(`/admin/services/${row.id}/renewal-candidates`)) : Promise.resolve(null),
  ])
  linked.value = d?.orders ?? []
  manualRenewals.value = d?.manual_renewals ?? []
  candidates.value = c?.items ?? []
}
const endChanged = computed(() => {
  const cur = editing.value?.ends_at
  return !!end.value && (!cur || end.value.getTime() !== new Date(cur).getTime())
})
async function update(cancel = false) {
  const cur = editing.value
  if (!cur) return
  const body = cancel
    ? { status: 'cancelled' }
    : {
        ...(endChanged.value && end.value ? { ends_at: end.value.toISOString() } : {}),
        email_reminders: cur.email_reminders,
        telegram_reminders: cur.telegram_reminders,
        ...(cur.standalone
          ? {
              ...(cur.label.trim() ? { label: cur.label.trim() } : {}),
              ...(cur.customer_label?.trim() ? { customer_label: cur.customer_label.trim() } : {}),
              ...(cur.contact?.trim() ? { contact: cur.contact.trim() } : {}),
              ...(cur.notes?.trim() ? { notes: cur.notes.trim() } : {}),
            }
          : {}),
      }
  const r = await run(() => client.patch(`/admin/services/${cur.id}`, { expected_version: cur.version, reason: reason.value, ...body }))
  if (r) {
    editing.value = null
    await load()
  }
}
async function renew() {
  const cur = editing.value
  if (!cur) return
  // standalone records are renewed by a staff confirmation alone (no order)
  const body = cur.standalone ? { reason: reason.value } : { order_id: renewal.value, reason: reason.value }
  const r = await run(() => client.post(`/admin/services/${cur.id}/renewals`, body))
  if (r) {
    editing.value = null
    await load()
  }
}
</script>
<template>
  <div class="page">
    <h1>{{ t('services.title') }}</h1>
    <p>{{ t('services.note') }}</p>
    <div class="tools">
      <el-button type="primary" @click="adding = true">{{ t('services.add') }}</el-button>
      <el-button type="primary" plain @click="recording = true">{{ t('servicesV11.newRecord') }}</el-button>
      <el-input v-model="q" clearable class="q" :placeholder="t('servicesV7.search')" @keyup.enter="search" @clear="search" />
      <el-select v-model="due" clearable :placeholder="t('services.filter')" @change="search">
        <el-option value="upcoming" :label="t('services.upcoming')" />
        <el-option value="overdue" :label="t('services.overdue')" />
      </el-select>
      <el-checkbox v-model="mine" @change="search">{{ t('services.mine') }}</el-checkbox>
      <el-button :loading="saving" @click="load">{{ t('ops.refresh') }}</el-button>
    </div>
    <el-table :data="rows" row-key="id">
      <el-table-column prop="ref" :label="t('servicesV11.ref')" width="120" />
      <el-table-column :label="t('servicesV11.customer')" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ row.customer_label || row.buyer_email || row.contact || '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('servicesV7.orderCol')" min-width="130">
        <template #default="{ row }">
          <el-link v-if="row.order_no" type="primary" @click="drawerOrder = row.order_id">{{ row.order_no }}</el-link>
        </template>
      </el-table-column>
      <el-table-column prop="label" :label="t('services.label')" min-width="150" show-overflow-tooltip />
      <el-table-column prop="product_title" :label="t('servicesV7.product')" min-width="140" show-overflow-tooltip />
      <el-table-column :label="t('ops.status')" min-width="150">
        <template #default="{ row }">
          {{ t(`ops.states.${row.status}`) }}
          <el-tag v-if="dueTag(row as Service)" size="small" :type="dueTag(row as Service)!.type as 'info'">{{ dueTag(row as Service)!.text }}</el-tag>
          <el-tag v-if="row.after_sales_open" size="small" type="danger">{{ t('servicesV7.afterSalesOpen') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('servicesV7.source')" width="110">
        <template #default="{ row }">{{ row.source === 'auto' ? t('servicesV7.auto') : row.standalone ? t('servicesV11.record') : t('servicesV7.manual') }}</template>
      </el-table-column>
      <el-table-column :label="t('services.end')" min-width="170">
        <template #default="{ row }">{{ when(row.ends_at) }}</template>
      </el-table-column>
      <el-table-column :label="t('gateways.actions')" min-width="190">
        <template #default="{ row }">
          <el-button v-if="row.status === 'pending'" @click="activate(row as Service)">{{ t('services.activate') }}</el-button>
          <el-button @click="edit(row as Service)">{{ t('services.manage') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev,pager,next" @current-change="load" />

    <el-dialog v-model="adding" :title="t('services.add')" width="min(94vw,620px)">
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item :label="t('servicesV7.orderNo')"><el-input v-model="form.order_no" maxlength="16" /></el-form-item>
        <p class="muted">{{ t('servicesV7.orderNoHint') }}</p>
        <el-form-item :label="`${t('services.label')} (${t('servicesV7.optional')})`"><el-input v-model="form.label" maxlength="120" /></el-form-item>
        <el-collapse>
          <el-collapse-item :title="t('servicesV7.byIds')" name="ids">
            <el-form-item :label="t('services.buyer')"><el-input v-model="form.buyer_id" /></el-form-item>
            <el-form-item :label="t('services.order')"><el-input v-model="form.order_id" /></el-form-item>
            <el-form-item :label="t('services.owner')"><el-input v-model="form.owner_id" :placeholder="auth.user?.id" /></el-form-item>
          </el-collapse-item>
        </el-collapse>
        <p>{{ t('services.activationNote') }}</p>
        <el-button type="primary" native-type="submit" :loading="saving">{{ t('common.save') }}</el-button>
      </el-form>
    </el-dialog>

    <el-dialog v-model="recording" :title="t('servicesV11.newRecord')" width="min(94vw,640px)">
      <el-form label-position="top" @submit.prevent="createRecord">
        <p class="muted">{{ t('servicesV11.recordNote') }}</p>
        <el-form-item :label="t('services.label')" required><el-input v-model="rec.label" maxlength="120" /></el-form-item>
        <el-form-item :label="t('servicesV11.customerLabel')"><el-input v-model="rec.customer_label" maxlength="120" /></el-form-item>
        <el-form-item :label="t('servicesV11.contact')"><el-input v-model="rec.contact" maxlength="200" /></el-form-item>
        <el-form-item :label="`${t('servicesV7.buyerEmail')} (${t('servicesV7.optional')})`"><el-input v-model="rec.buyer_email" maxlength="254" /></el-form-item>
        <el-form-item :label="`${t('servicesV7.orderNo')} (${t('servicesV7.optional')})`"><el-input v-model="rec.order_no" maxlength="16" /></el-form-item>
        <el-form-item :label="t('servicesV11.start')"><el-date-picker v-model="recStart" type="datetime" /></el-form-item>
        <el-form-item :label="t('servicesV11.endMode')">
          <el-radio-group v-model="rec.endMode">
            <el-radio-button value="month">{{ t('servicesV11.oneMonth') }}</el-radio-button>
            <el-radio-button value="days">{{ t('servicesV11.days') }}</el-radio-button>
            <el-radio-button value="date">{{ t('servicesV11.endDate') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="rec.endMode === 'days'" :label="t('servicesV11.days')"><el-input-number v-model="rec.days" :min="1" :max="366" /></el-form-item>
        <el-form-item v-if="rec.endMode === 'date'" :label="t('servicesV11.endDate')"><el-date-picker v-model="recEnd" type="datetime" /></el-form-item>
        <el-form-item :label="t('servicesV11.timezone')"><el-input v-model="rec.timezone" maxlength="64" /></el-form-item>
        <el-form-item :label="t('servicesV11.leadDays')"><el-input-number v-model="rec.lead" :min="1" :max="60" :placeholder="t('servicesV11.leadDefault')" /></el-form-item>
        <el-checkbox v-model="rec.email">{{ t('services.email') }}</el-checkbox>
        <el-checkbox v-model="rec.telegram">{{ t('services.telegram') }}</el-checkbox>
        <el-form-item :label="t('servicesV11.notes')" class="gap"><el-input v-model="rec.notes" type="textarea" :rows="3" maxlength="4000" /></el-form-item>
        <el-button type="primary" native-type="submit" :disabled="!recValid" :loading="saving">{{ t('common.save') }}</el-button>
      </el-form>
    </el-dialog>

    <el-dialog :model-value="!!editing" :title="t('services.manage')" width="min(94vw,680px)" @close="editing = null">
      <el-form v-if="editing" label-position="top" @submit.prevent="update()">
        <p class="muted">{{ t('servicesV11.ref') }}: <b>{{ editing.ref }}</b></p>
        <template v-if="editing.standalone">
          <el-form-item :label="t('services.label')"><el-input v-model="editing.label" maxlength="120" /></el-form-item>
          <el-form-item :label="t('servicesV11.customerLabel')"><el-input v-model="editing.customer_label" maxlength="120" /></el-form-item>
          <el-form-item :label="t('servicesV11.contact')"><el-input v-model="editing.contact" maxlength="200" /></el-form-item>
          <el-form-item :label="t('servicesV11.notes')"><el-input v-model="editing.notes" type="textarea" :rows="3" maxlength="4000" /></el-form-item>
        </template>
        <h4 v-if="linked.length">{{ t('servicesV7.linked') }}</h4>
        <el-table v-if="linked.length" :data="linked" size="small">
          <el-table-column :label="t('servicesV7.orderCol')" min-width="120">
            <template #default="{ row }"><el-link type="primary" @click="drawerOrder = row.order_id">{{ row.order_no }}</el-link></template>
          </el-table-column>
          <el-table-column :label="t('servicesV7.kindSource')" width="100">
            <template #default="{ row }">{{ row.kind === 'source' ? t('servicesV7.kindSource') : t('servicesV7.kindRenewal') }}</template>
          </el-table-column>
          <el-table-column :label="t('servicesV7.paidAt')" min-width="150"><template #default="{ row }">{{ when(row.paid_at) }}</template></el-table-column>
          <el-table-column :label="t('servicesV7.amount')" width="110"><template #default="{ row }">{{ row.amount }} {{ row.currency }}</template></el-table-column>
          <el-table-column prop="status" :label="t('servicesV7.orderStatus')" width="110" />
          <el-table-column prop="after_sales_status" :label="t('servicesV7.asStatus')" width="100" />
        </el-table>
        <template v-if="manualRenewals.length">
          <h4>{{ t('servicesV11.manualRenewals') }}</h4>
          <ul class="muted">
            <li v-for="(m, i) in manualRenewals" :key="i">{{ when(m.starts_at) }} → {{ when(m.ends_at) }}{{ m.note ? ` · ${m.note}` : '' }}</li>
          </ul>
        </template>
        <el-form-item :label="t('services.reason')" class="gap"><el-input v-model="reason" maxlength="200" /></el-form-item>
        <el-form-item :label="t('servicesV7.endPicker')"><el-date-picker v-model="end" type="datetime" /></el-form-item>
        <el-checkbox v-model="editing.email_reminders">{{ t('services.email') }}</el-checkbox>
        <el-checkbox v-model="editing.telegram_reminders">{{ t('services.telegram') }}</el-checkbox>
        <div class="tools">
          <el-button native-type="submit" :disabled="!reason" :loading="saving">{{ t('common.save') }}</el-button>
          <el-button type="danger" :disabled="!reason" :loading="saving" @click="update(true)">{{ t('services.cancel') }}</el-button>
        </div>
        <el-divider />
        <p>{{ t('services.renewalNote') }}</p>
        <el-form-item v-if="!editing.standalone" :label="t('servicesV7.pickRenewal')">
          <el-select v-model="renewal" style="width: 100%" :no-data-text="t('servicesV7.noCandidates')">
            <el-option v-for="c in candidates" :key="c.order_id" :value="c.order_id" :label="`${c.order_no} · ${when(c.paid_at)} · ${c.amount} ${c.currency}`" />
          </el-select>
        </el-form-item>
        <el-button :disabled="!reason || (!editing.standalone && !renewal)" :loading="saving" @click="renew">{{ t('services.renew') }}</el-button>
      </el-form>
    </el-dialog>
    <OrderDrawer :order-id="drawerOrder" @close="drawerOrder = null" />
  </div>
</template>
<style scoped>
.tools {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin: 12px 0;
}
.tools .el-select {
  max-width: 230px;
}
.tools .q {
  max-width: 280px;
}
.gap {
  margin-top: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
