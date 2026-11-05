<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Lock } from '@element-plus/icons-vue'
import { formatDateTime, type AdminProduct, type CardImportResult, type CardRow, type CardStatus } from '@24goods/shared'
import { api } from '../api'
import { usePaged } from '../composables/usePaged'
import { confirmBox, toastError } from '../ui'

const MAX_LINES = 5000
const { t, locale } = useI18n()
const products = ref<AdminProduct[]>([])
const searching = ref(false)
const productId = ref('')
const lines = ref('')
const importing = ref(false)
const result = ref<CardImportResult | null>(null)
const cardStatus = ref<CardStatus | ''>('')

const selected = computed(() => products.value.find((p) => p.id === productId.value) ?? null)
const titleOf = (p: AdminProduct) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en
const lineCount = computed(() => lines.value.split(/\r?\n/).filter((l) => l.trim()).length)
const tooMany = computed(() => lineCount.value > MAX_LINES)

async function searchProducts(q = '') {
  searching.value = true
  try {
    const r = await api.products.list({ q: q || undefined, page: 1, page_size: 50 })
    const autos = r.items.filter((p) => p.delivery_mode === 'auto_card')
    // keep the selected product in the options
    const keep = selected.value && !autos.some((p) => p.id === selected.value!.id) ? [selected.value] : []
    products.value = [...keep, ...autos]
  } catch (e) {
    toastError(e)
  } finally {
    searching.value = false
  }
}
onMounted(() => void searchProducts())

const cards = usePaged<CardRow>((page, page_size) =>
  api.products.cards(productId.value, { status: cardStatus.value, page, page_size }),
)
watch([productId, cardStatus], () => {
  result.value = null
  if (productId.value) void cards.reload()
})

async function doImport() {
  const p = selected.value
  if (!p || !lineCount.value || tooMany.value || importing.value) return
  if (!(await confirmBox(t('inventory.confirmImport', { n: lineCount.value, title: titleOf(p) })))) return
  importing.value = true
  try {
    result.value = await api.products.importCards(p.id, lines.value)
    // Never keep plaintext codes around in the page once they are sealed server-side.
    lines.value = ''
    void cards.reload()
  } catch (e) {
    toastError(e)
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div class="page">
    <el-alert :title="t('inventory.securityNote')" type="info" show-icon :closable="false" class="note">
      <template #icon><el-icon><Lock /></el-icon></template>
    </el-alert>

    <div class="panel block">
      <el-select
        v-model="productId"
        filterable
        remote
        :remote-method="searchProducts"
        :loading="searching"
        :placeholder="t('inventory.selectProduct')"
        style="width: min(480px, 100%)"
      >
        <el-option v-for="p in products" :key="p.id" :value="p.id" :label="titleOf(p)">
          <span>{{ titleOf(p) }}</span>
          <span class="muted opt-stock">{{ p.stock ?? 0 }}</span>
        </el-option>
      </el-select>
      <div class="muted small hint">{{ t('inventory.onlyAuto') }}</div>
    </div>

    <template v-if="selected">
      <div class="panel block">
        <h2 class="h2">{{ t('inventory.importTitle') }}</h2>
        <el-input
          v-model="lines"
          type="textarea"
          :rows="8"
          :placeholder="t('inventory.placeholder')"
          spellcheck="false"
          autocomplete="off"
          class="mono"
        />
        <div class="import-bar">
          <span :class="tooMany ? 'err' : 'muted'">
            {{ t('inventory.lines', lineCount) }}<template v-if="tooMany"> — {{ t('inventory.tooMany') }}</template>
          </span>
          <el-button type="primary" :disabled="!lineCount || tooMany" :loading="importing" @click="doImport">
            {{ t('inventory.import') }}
          </el-button>
        </div>
        <el-alert v-if="result" type="success" :title="t('inventory.resultTitle')" show-icon class="result" @close="result = null">
          <div class="result-row">
            <el-statistic :title="t('inventory.imported')" :value="result.imported" />
            <el-statistic :title="t('inventory.duplicates')" :value="result.duplicates" />
            <el-statistic :title="t('inventory.rejected')" :value="result.rejected" />
          </div>
          <p class="small">{{ t('inventory.result', { ...result }) }}</p>
          <p class="small muted">{{ t('inventory.cleared') }}</p>
        </el-alert>
      </div>

      <div class="panel">
        <div class="page-head">
          <h2 class="h2">{{ t('inventory.cards') }}</h2>
          <el-select v-model="cardStatus" style="width: 160px">
            <el-option value="" :label="t('inventory.allStatus')" />
            <el-option v-for="s in ['available', 'reserved', 'sold', 'void'] as const" :key="s" :value="s" :label="t(`inventory.cardStatus.${s}`)" />
          </el-select>
        </div>
        <el-table v-loading="cards.loading.value" :data="cards.items.value" :empty-text="t('common.empty')">
          <el-table-column :label="t('inventory.masked')" min-width="220">
            <template #default="{ row }"><span class="mono">{{ row.masked }}</span></template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'available' ? 'success' : row.status === 'void' ? 'danger' : 'info'">
                {{ t(`inventory.cardStatus.${row.status}`) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('inventory.order')" min-width="160">
            <template #default="{ row }">
              <RouterLink v-if="row.order_id" :to="{ path: '/orders', query: { open: row.order_id } }" class="mono small">{{ row.order_id }}</RouterLink>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.createdAt')" width="170">
            <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination
            layout="total, prev, pager, next"
            :total="cards.total.value"
            :page-size="cards.pageSize"
            :current-page="cards.page.value"
            @current-change="cards.load"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.note,
.block {
  margin-bottom: 16px;
}
.hint {
  margin-top: 6px;
}
.small {
  font-size: 12px;
}
.opt-stock {
  float: right;
  font-size: 12px;
}
.h2 {
  font-size: 16px;
  margin: 0 0 12px;
}
.import-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}
.err {
  color: var(--el-color-danger);
}
.result {
  margin-top: 12px;
}
.result-row {
  display: flex;
  gap: 32px;
  margin: 6px 0;
}
.result p {
  margin: 4px 0 0;
}
</style>
