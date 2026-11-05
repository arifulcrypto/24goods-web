<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDateTime, formatMoney, type LedgerRow } from '@24goods/shared'
import { api } from '../api'
import { usePaged } from '../composables/usePaged'

const { t, locale } = useI18n()
const table = usePaged<LedgerRow>((page, page_size) => api.ledger({ page, page_size }), 50)
onMounted(() => void table.load())
</script>

<template>
  <div class="page">
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" :empty-text="t('common.empty')" size="small">
        <el-table-column :label="t('ledger.at')" width="170">
          <template #default="{ row }">{{ formatDateTime(row.at, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('ledger.txn')" min-width="160">
          <template #default="{ row }"><span class="mono">{{ row.txn_id }}</span></template>
        </el-table-column>
        <el-table-column :label="t('ledger.account')" prop="account" min-width="160" />
        <el-table-column :label="t('ledger.amount')" width="140" align="right">
          <template #default="{ row }">
            <span class="mono" :class="row.amount.startsWith('-') ? 'neg' : 'pos'">{{ formatMoney(row.amount, row.currency, locale) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('ledger.memo')" prop="memo" min-width="200" show-overflow-tooltip />
        <el-table-column :label="t('ledger.order')" min-width="160">
          <template #default="{ row }">
            <RouterLink v-if="row.order_id" :to="{ path: '/orders', query: { open: row.order_id } }" class="mono">{{ row.order_id }}</RouterLink>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.neg {
  color: var(--el-color-danger);
}
.pos {
  color: var(--el-color-success);
}
</style>
