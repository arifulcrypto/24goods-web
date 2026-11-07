<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, Search } from '@element-plus/icons-vue'
import { formatDateTime } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AuditFilters, AuditRowV5 } from '../api-v5'
import { useAuthStore } from '../stores/auth'
import { usePaged } from '../composables/usePaged'
import { auditSides, rangeToQuery } from '../lib/auditRow'
import { jsonDiff, showValue } from '../lib/diff'

const { t, locale } = useI18n()
const auth = useAuthStore()
const f = reactive({ action: '', actor: '', target: '', severity: '' })
const range = ref<[Date, Date] | null>(null)
const filters = computed<AuditFilters>(() => ({
  action: f.action.trim() || undefined,
  actor: f.actor.trim() || undefined,
  target: f.target.trim() || undefined,
  severity: f.severity || undefined,
  ...rangeToQuery(range.value),
}))
const table = usePaged<AuditRowV5>((page, page_size) => apiV5.audit.list({ ...filters.value, page, page_size }), 50)
onMounted(() => void table.load())
const csvHref = computed(() => apiV5.audit.csvUrl(filters.value))
function reset() {
  Object.assign(f, { action: '', actor: '', target: '', severity: '' })
  range.value = null
  void table.reload()
}

const open = ref(false)
const cur = ref<AuditRowV5 | null>(null)
const sides = computed(() => (cur.value ? auditSides(cur.value) : { before: null, after: null }))
const diff = computed(() => jsonDiff(sides.value.before, sides.value.after))
const pretty = (v: unknown) => (v === null || v === undefined ? '' : JSON.stringify(v, null, 2))
function show(r: AuditRowV5) {
  cur.value = r
  open.value = true
}
const SEV: Record<string, 'info' | 'warning' | 'danger'> = { info: 'info', warning: 'warning', warn: 'warning', critical: 'danger', error: 'danger' }
</script>

<template>
  <div class="page">
    <div class="page-head filters">
      <el-input v-model="f.action" :placeholder="t('v5.audit.actionPrefix')" clearable style="width: 190px" @keyup.enter="table.reload()" />
      <el-input v-model="f.actor" :placeholder="t('v5.audit.actorSearch')" clearable style="width: 190px" @keyup.enter="table.reload()" />
      <el-input v-model="f.target" :placeholder="t('v5.audit.targetSearch')" clearable style="width: 170px" @keyup.enter="table.reload()" />
      <el-select v-model="f.severity" clearable style="width: 130px" :placeholder="t('v5.audit.severity')">
        <el-option v-for="s in ['info', 'warning', 'critical']" :key="s" :value="s" :label="t(`v5.audit.sev.${s}`)" />
      </el-select>
      <el-date-picker v-model="range" type="datetimerange" :start-placeholder="t('v5.promo.start')" :end-placeholder="t('v5.promo.end')" format="YYYY-MM-DD HH:mm" style="width: 340px" />
      <el-button type="primary" :icon="Search" @click="table.reload()">{{ t('v5.audit.search') }}</el-button>
      <el-button @click="reset">{{ t('v5.audit.reset') }}</el-button>
      <a :href="csvHref" download class="csv"><el-button :icon="Download">{{ t('v5.audit.csv') }}</el-button></a>
    </div>
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" :empty-text="t('common.empty')" size="small" class="clickable" @row-click="show">
        <el-table-column :label="t('audit.at')" width="170"><template #default="{ row }">{{ formatDateTime(row.at, locale) }}</template></el-table-column>
        <el-table-column :label="t('audit.actor')" prop="actor" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('audit.action')" min-width="170">
          <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.action }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('v5.audit.severity')" width="100">
          <template #default="{ row }"><el-tag v-if="row.severity" size="small" :type="SEV[row.severity] ?? 'info'">{{ row.severity }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('audit.target')" prop="target" min-width="200" show-overflow-tooltip />
        <el-table-column :label="t('audit.ip')" width="140"><template #default="{ row }"><span class="mono">{{ row.ip ?? '—' }}</span>
            <router-link v-if="row.ip && auth.isAdmin" class="blockip" :to="{ name: 'users', query: { tab: 'blocked', ip: row.ip } }" @click.stop>{{ t('v5.users.blockIp') }}</router-link>
          </template></el-table-column>
      </el-table>
      <div class="pager"><el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" /></div>
    </div>

    <el-drawer v-model="open" :title="cur?.action ?? ''" size="min(680px, 96vw)">
      <template v-if="cur">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="t('audit.at')">{{ formatDateTime(cur.at, locale) }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.actor')">{{ cur.actor }}</el-descriptions-item>
          <el-descriptions-item :label="t('audit.target')"><span class="mono">{{ cur.target }}</span></el-descriptions-item>
          <el-descriptions-item :label="t('audit.ip')"><span class="mono">{{ cur.ip ?? '—' }}</span></el-descriptions-item>
        </el-descriptions>
        <h3 class="sec">{{ t('v5.audit.changes') }}</h3>
        <el-table v-if="diff.length" :data="diff" size="small" border>
          <el-table-column :label="t('v5.audit.field')" prop="path" min-width="140" class-name="mono" />
          <el-table-column :label="t('v5.audit.before')" min-width="150"><template #default="{ row }"><span :class="{ del: row.kind !== 'added' }" class="mono val">{{ showValue(row.before) }}</span></template></el-table-column>
          <el-table-column :label="t('v5.audit.after')" min-width="150"><template #default="{ row }"><span :class="{ add: row.kind !== 'removed' }" class="mono val">{{ showValue(row.after) }}</span></template></el-table-column>
        </el-table>
        <p v-else class="muted">{{ t('v5.audit.noChanges') }}</p>
        <el-collapse class="raw">
          <el-collapse-item :title="t('v5.audit.raw')" name="raw">
            <div class="cols">
              <div><div class="muted small">{{ t('v5.audit.before') }}</div><pre class="detail mono">{{ pretty(sides.before) }}</pre></div>
              <div><div class="muted small">{{ t('v5.audit.after') }}</div><pre class="detail mono">{{ pretty(sides.after) }}</pre></div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.blockip {
  margin-left: 8px;
  font-size: 12px;
}
.filters {
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-start;
}
.csv {
  text-decoration: none;
}
.clickable :deep(.el-table__row) {
  cursor: pointer;
}
.sec {
  font-size: 14px;
  margin: 16px 0 8px;
}
.small {
  font-size: 12px;
}
.val {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.del {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
.add {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}
.raw {
  margin-top: 12px;
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.detail {
  margin: 0;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  max-height: 280px;
  overflow: auto;
}
</style>
