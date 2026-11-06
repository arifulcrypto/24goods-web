<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDateTime } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminPayout, PayoutStatus } from '../api-v5'
import { usePaged } from '../composables/usePaged'
import { minorToYuan } from '../lib/money'
import { toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const status = ref<'' | PayoutStatus>('requested')
const table = usePaged<AdminPayout>((page, page_size) => apiV5.payouts.list({ status: status.value, page, page_size }))
watch(status, () => void table.reload())
onMounted(() => void table.load())

const dlg = ref(false)
const target = ref<AdminPayout | null>(null)
const action = ref<'paid' | 'rejected'>('paid')
const note = ref('')
const err = ref('')
const busy = ref(false)
const TAG: Record<PayoutStatus, 'warning' | 'success' | 'danger'> = { requested: 'warning', paid: 'success', rejected: 'danger' }

function decide(p: AdminPayout, a: 'paid' | 'rejected') {
  target.value = p
  action.value = a
  note.value = ''
  err.value = ''
  dlg.value = true
}
async function submit() {
  if (!target.value || busy.value) return
  if (action.value === 'rejected' && !note.value.trim()) {
    err.value = t('v5.payouts.noteRequired')
    return
  }
  busy.value = true
  try {
    await apiV5.payouts.decide(target.value.id, { action: action.value, note: note.value.trim() })
    toastOk(t('v5.payouts.done'))
    dlg.value = false
    void table.load()
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <el-radio-group v-model="status">
        <el-radio-button v-for="s in (['requested', 'paid', 'rejected', ''] as const)" :key="s" :value="s">{{ s ? t(`v5.payouts.status.${s}`) : t('orders.allStatus') }}</el-radio-button>
      </el-radio-group>
    </div>
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('v5.resellers.store')" min-width="170">
          <template #default="{ row }"><div>{{ row.store_name ?? '—' }}</div><div class="muted small mono">{{ row.slug }}</div></template>
        </el-table-column>
        <el-table-column :label="t('orders.col.amount')" width="120"><template #default="{ row }"><span class="price">¥{{ minorToYuan(row.amount_minor) }}</span></template></el-table-column>
        <el-table-column :label="t('v5.payouts.account')" min-width="220">
          <template #default="{ row }">
            <template v-if="row.account_snapshot">
              <div>{{ row.account_snapshot.method }} · {{ row.account_snapshot.name }}</div>
              <div class="mono small">{{ row.account_snapshot.account }}</div>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.status')" width="120"><template #default="{ row }"><el-tag size="small" :type="TAG[row.status as PayoutStatus]">{{ t(`v5.payouts.status.${row.status}`) }}</el-tag></template></el-table-column>
        <el-table-column :label="t('v5.payouts.note')" prop="note" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('v5.payouts.requestedAt')" width="160"><template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template></el-table-column>
        <el-table-column :label="t('common.actions')" width="170" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'requested'">
              <el-button link type="success" @click="decide(row as AdminPayout, 'paid')">{{ t('v5.payouts.markPaid') }}</el-button>
              <el-button link type="danger" @click="decide(row as AdminPayout, 'rejected')">{{ t('v5.payouts.reject') }}</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager"><el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" /></div>
    </div>
    <el-dialog v-model="dlg" :title="action === 'paid' ? t('v5.payouts.markPaid') : t('v5.payouts.reject')" width="min(440px, 96vw)">
      <p v-if="target">{{ target.store_name }} · <strong>¥{{ minorToYuan(target.amount_minor) }}</strong></p>
      <el-alert v-if="action === 'paid'" type="info" :closable="false" :title="t('v5.payouts.paidHint')" class="gap" />
      <el-form-item :label="t('v5.payouts.note')" :error="err">
        <el-input v-model="note" type="textarea" :rows="3" maxlength="200" show-word-limit :placeholder="action === 'paid' ? t('v5.payouts.notePaid') : t('v5.payouts.noteReject')" />
      </el-form-item>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button :type="action === 'paid' ? 'success' : 'danger'" :loading="busy" @click="submit">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.small {
  font-size: 12px;
}
.gap {
  margin-bottom: 12px;
}
</style>
