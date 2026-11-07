<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'
import { copyText, formatDateTime } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminReseller, AdminResellerDomain, ResellerStatus } from '../api-v5'
import MarkupInput from '../components/MarkupInput.vue'
import { usePaged } from '../composables/usePaged'
import { minorToYuan } from '../lib/money'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const status = ref<'' | ResellerStatus>('')
const q = ref('')
const table = usePaged<AdminReseller>((page, page_size) => apiV5.resellers.list({ status: status.value, q: q.value.trim() || undefined, page, page_size }))
let debounce: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void table.reload(), 300)
})
watch(status, () => void table.reload())
const domains = ref<AdminResellerDomain[]>([])
async function loadDomains() {
  try {
    domains.value = await apiV5.resellerDomains.list()
  } catch (e) {
    toastError(e)
  }
}
async function removeDomain(d: AdminResellerDomain) {
  if (!(await confirmBox(t('v5.resellers.domainRemoveConfirm', { host: d.hostname })))) return
  try {
    await apiV5.resellerDomains.remove(d.id)
    await loadDomains()
  } catch (e) {
    toastError(e)
  }
}
onMounted(() => {
  void table.load()
  void loadDomains()
})

const open = ref(false)
const cur = ref<AdminReseller | null>(null)
const mk = ref({ kind: 'percent' as 'percent' | 'fixed', value: 0 })
const busy = ref(false)
const TAG: Record<ResellerStatus, 'success' | 'warning' | 'danger'> = { active: 'success', pending_payment: 'warning', suspended: 'danger' }
const yuan = (m?: number) => `¥${minorToYuan(m ?? 0)}`
const markupText = (r: AdminReseller) => (r.markup_kind === 'percent' ? `${r.markup_value / 10}%` : yuan(r.markup_value))

async function show(r: AdminReseller) {
  cur.value = r
  open.value = true
  try {
    cur.value = await apiV5.resellers.get(r.id)
  } catch (e) {
    toastError(e)
  }
  mk.value = { kind: cur.value.markup_kind, value: cur.value.markup_value }
}
async function patch(b: Parameters<typeof apiV5.resellers.update>[1]) {
  if (!cur.value || busy.value) return
  busy.value = true
  try {
    cur.value = await apiV5.resellers.update(cur.value.id, b)
    toastOk(t('v5.resellers.saved'))
    void table.load()
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
async function setStatus(s: ResellerStatus) {
  const key = s === 'suspended' ? 'confirmSuspend' : cur.value?.status === 'pending_payment' ? 'confirmWaive' : 'confirmActivate'
  if (!(await confirmBox(t(`v5.resellers.${key}`, { name: cur.value?.store_name ?? '' })))) return
  await patch({ status: s })
}
async function copy(u: string) {
  if (await copyText(u)) toastOk(t('products.linkCopied'))
}
const storeUrl = (r: AdminReseller) => r.store_url ?? ''
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="toolbar">
        <el-radio-group v-model="status">
          <el-radio-button value="">{{ t('orders.allStatus') }}</el-radio-button>
          <el-radio-button v-for="s in (['active', 'pending_payment', 'suspended'] as const)" :key="s" :value="s">{{ t(`v5.resellers.status.${s}`) }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="q" :placeholder="t('v5.resellers.search')" :prefix-icon="Search" clearable style="width: 240px" />
      </div>
    </div>
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')" class="clickable" @row-click="show">
        <el-table-column :label="t('v5.resellers.store')" min-width="200">
          <template #default="{ row }"><div>{{ row.store_name }}</div><div class="muted small mono">{{ row.slug }}</div></template>
        </el-table-column>
        <el-table-column :label="t('v5.resellers.owner')" prop="user_email" min-width="190" show-overflow-tooltip />
        <el-table-column :label="t('products.col.status')" width="130">
          <template #default="{ row }"><el-tag size="small" :type="TAG[row.status as ResellerStatus]">{{ t(`v5.resellers.status.${row.status}`) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('v5.resellers.markup')" width="110"><template #default="{ row }">{{ markupText(row as AdminReseller) }}</template></el-table-column>
        <el-table-column :label="t('v5.resellers.orders')" width="90"><template #default="{ row }">{{ row.stats?.orders ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('v5.resellers.earned')" width="120"><template #default="{ row }">{{ row.stats?.markup_minor != null ? yuan(row.stats.markup_minor) : '—' }}</template></el-table-column>
        <el-table-column :label="t('v5.resellers.activatedAt')" width="160"><template #default="{ row }">{{ row.activated_at ? formatDateTime(row.activated_at, locale) : '—' }}</template></el-table-column>
      </el-table>
      <div class="pager"><el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" /></div>
    </div>

    <div v-if="domains.length" class="panel">
      <h3 class="sec pad">{{ t('v5.resellers.domains') }}</h3>
      <el-table :data="domains" row-key="id">
        <el-table-column :label="t('v5.resellers.domainHost')" prop="hostname" min-width="220" show-overflow-tooltip />
        <el-table-column :label="t('v5.resellers.store')" prop="reseller_slug" width="160" />
        <el-table-column :label="t('products.col.status')" width="130">
          <template #default="{ row }"><el-tag size="small" :type="row.status === 'active' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'">{{ t(`v5.resellers.domainStatus.${row.status}`) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('v5.resellers.domainSsl')" prop="ssl_status" width="170" />
        <el-table-column width="110">
          <template #default="{ row }"><el-button link type="danger" @click="removeDomain(row as AdminResellerDomain)">{{ t('v5.resellers.domainRemove') }}</el-button></template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="open" :title="cur?.store_name ?? ''" size="min(520px, 96vw)">
      <template v-if="cur">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="t('v5.resellers.slug')"><span class="mono">{{ cur.slug }}</span></el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.owner')">{{ cur.user_email ?? '—' }}</el-descriptions-item>
          <el-descriptions-item :label="t('products.col.status')"><el-tag size="small" :type="TAG[cur.status]">{{ t(`v5.resellers.status.${cur.status}`) }}</el-tag></el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.storeLink')">
            <template v-if="storeUrl(cur)">
              <a :href="storeUrl(cur)" target="_blank" rel="noopener noreferrer" class="mono">{{ storeUrl(cur) }}</a>
              <el-button link type="primary" @click="copy(storeUrl(cur))">{{ t('products.copyLink') }}</el-button>
            </template>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.activatedAt')">{{ cur.activated_at ? formatDateTime(cur.activated_at, locale) : '—' }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sec">{{ t('v5.resellers.stats') }}</h3>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="t('v5.resellers.orders')">{{ cur.stats?.orders ?? 0 }}</el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.revenue')">{{ yuan(cur.stats?.revenue_minor) }}</el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.earned')">{{ yuan(cur.stats?.markup_minor) }}</el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.pending')">{{ yuan(cur.stats?.pending_minor) }}</el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.available')">{{ yuan(cur.stats?.available_minor) }}</el-descriptions-item>
          <el-descriptions-item :label="t('v5.resellers.paid')">{{ yuan(cur.stats?.paid_minor) }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="sec">{{ t('v5.resellers.markup') }}</h3>
        <MarkupInput v-model:kind="mk.kind" v-model:value="mk.value" />
        <p class="muted small">{{ t('v5.resellers.markupHint') }}</p>
        <el-button type="primary" :loading="busy" @click="patch({ markup_kind: mk.kind, markup_value: mk.value })">{{ t('common.save') }}</el-button>
        <el-divider />
        <el-button v-if="cur.status !== 'active'" type="success" :loading="busy" @click="setStatus('active')">{{ cur.status === 'pending_payment' ? t('v5.resellers.activateWaive') : t('v5.resellers.activate') }}</el-button>
        <el-button v-if="cur.status === 'active'" type="danger" plain :loading="busy" @click="setStatus('suspended')">{{ t('v5.resellers.suspend') }}</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.small {
  font-size: 12px;
}
.pad {
  padding: 0 12px;
}
.sec {
  font-size: 14px;
  margin: 16px 0 8px;
}
.clickable :deep(.el-table__row) {
  cursor: pointer;
}
</style>
