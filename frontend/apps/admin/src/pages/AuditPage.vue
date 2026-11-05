<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatDateTime, type AuditRow } from '@24goods/shared'
import { api } from '../api'
import { usePaged } from '../composables/usePaged'

const { t, locale } = useI18n()
const table = usePaged<AuditRow>((page, page_size) => api.audit({ page, page_size }), 50)
onMounted(() => void table.load())

/** Detail is arbitrary JSON from the server: always rendered as text, never HTML. */
function detailText(d: unknown): string {
  if (d === null || d === undefined) return ''
  if (typeof d === 'string') return d
  try {
    return JSON.stringify(d, null, 2)
  } catch {
    return String(d)
  }
}
</script>

<template>
  <div class="page">
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" :empty-text="t('common.empty')" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="detail mono">{{ detailText(row.detail) }}</pre>
          </template>
        </el-table-column>
        <el-table-column :label="t('audit.at')" width="170">
          <template #default="{ row }">{{ formatDateTime(row.at, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('audit.actor')" prop="actor" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('audit.action')" min-width="160">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.action }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('audit.target')" prop="target" min-width="200" show-overflow-tooltip />
        <el-table-column :label="t('audit.ip')" width="140">
          <template #default="{ row }"><span class="mono">{{ row.ip ?? '—' }}</span></template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail {
  margin: 0 16px;
  padding: 10px;
  background: #f8fafc;
  border-radius: 6px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
}
</style>
