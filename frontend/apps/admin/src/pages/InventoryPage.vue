<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Lock, Upload } from '@element-plus/icons-vue'
import { formatDateTime } from '@24goods/shared'
import type { AdminVariant } from '@24goods/shared'
import { api, apiV3 } from '../api'
import type { AdminProductV3, CardBatch, CardRowV3 } from '../api-v3'
import CardFindPanel from '../components/cards/CardFindPanel.vue'
import CardImportDialog from '../components/cards/CardImportDialog.vue'
import CardReasonDialog from '../components/cards/CardReasonDialog.vue'
import CardReplaceDialog from '../components/cards/CardReplaceDialog.vue'
import { usePaged } from '../composables/usePaged'
import { CARD_STATUSES, canVoid, daysUntil, emptyCardFilters } from '../lib/cards'
import { toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const products = ref<AdminProductV3[]>([])
const searching = ref(false)
const productId = ref('')
const batches = ref<CardBatch[]>([])
const filters = reactive(emptyCardFilters())
const importOpen = ref(false)
const selection = ref<CardRowV3[]>([])
const voidDlg = reactive({ open: false, busy: false, kind: 'ids' as 'ids' | 'batch', batch: null as CardBatch | null })
const replaceDlg = reactive({ open: false, id: null as string | null, masked: '' })

const selected = computed(() => products.value.find((p) => p.id === productId.value) ?? null)
const titleOf = (p: AdminProductV3) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en
const voidable = computed(() => selection.value.filter((r) => canVoid(r.status)))

async function searchProducts(q = '') {
  searching.value = true
  try {
    const r = await apiV3.products.list({ q: q || undefined, page: 1, page_size: 50 })
    const autos = r.items.filter((p) => p.delivery_mode === 'auto_card')
    const keep = selected.value && !autos.some((p) => p.id === selected.value!.id) ? [selected.value] : []
    products.value = [...keep, ...autos]
  } catch (e) {
    toastError(e)
  } finally {
    searching.value = false
  }
}
onMounted(() => void searchProducts())

const cards = usePaged<CardRowV3>((page, page_size) => apiV3.products.cards(productId.value, filters, page, page_size))

async function loadBatches() {
  if (!productId.value) return
  try {
    batches.value = await apiV3.products.batches(productId.value)
  } catch (e) {
    toastError(e)
  }
}
function refresh() {
  selection.value = []
  void cards.reload()
  void loadBatches()
}
/** v11 W1: variants of the selected product (filter + column). */
const variants = ref<AdminVariant[]>([])
const variantName = (v: AdminVariant) => (locale.value === 'zh-CN' && v.name_i18n['zh-CN']) || v.name_i18n.en
const variantLabel = (id: string | null | undefined) => {
  const v = id ? variants.value.find((x) => x.id === id) : null
  return v ? variantName(v) : '—'
}
async function loadVariants() {
  variants.value = []
  if (!productId.value) return
  try {
    variants.value = (await api.products.variants.list(productId.value)).items
  } catch (e) {
    toastError(e)
  }
}
watch(productId, () => {
  Object.assign(filters, emptyCardFilters())
  batches.value = []
  void loadVariants()
  if (productId.value) refresh()
})
let deb: ReturnType<typeof setTimeout> | null = null
watch(
  filters,
  () => {
    if (!productId.value) return
    if (deb) clearTimeout(deb)
    deb = setTimeout(() => {
      selection.value = []
      void cards.reload()
    }, 300)
  },
  { deep: true },
)

function viewBatch(b: CardBatch) {
  filters.batch_id = filters.batch_id === b.id ? '' : b.id
}
function askVoidBatch(b: CardBatch) {
  voidDlg.kind = 'batch'
  voidDlg.batch = b
  voidDlg.open = true
}
function askVoidSelected() {
  voidDlg.kind = 'ids'
  voidDlg.batch = null
  voidDlg.open = true
}
async function doVoid(reason: string) {
  voidDlg.busy = true
  try {
    const r =
      voidDlg.kind === 'batch' && voidDlg.batch
        ? await apiV3.cards.voidBatch(voidDlg.batch.id, reason)
        : await apiV3.cards.voidIds(
            voidable.value.map((x) => x.id),
            reason,
          )
    toastOk(t('cards.voided', { n: r.voided }))
    voidDlg.open = false
    refresh()
  } catch (e) {
    toastError(e)
  } finally {
    voidDlg.busy = false
  }
}
function askReplace(r: CardRowV3) {
  replaceDlg.id = r.id
  replaceDlg.masked = r.masked
  replaceDlg.open = true
}
const voidMessage = computed(() =>
  voidDlg.kind === 'batch' && voidDlg.batch
    ? t('cards.voidBatchMsg', { n: voidDlg.batch.available + (voidDlg.batch.reserved ?? 0) })
    : t('cards.voidSelectedMsg', { n: voidable.value.length }),
)
const batchLabel = (b: CardBatch) => `${b.note || t('cards.noNote')} · ${b.id.slice(0, 6)}`
function orderLink(r: CardRowV3) {
  return r.order_id ? { path: '/orders', query: { open: r.order_id } } : { path: '/orders', query: { q: r.order_no ?? undefined } }
}
</script>

<template>
  <div class="page">
    <el-alert :title="t('inventory.securityNote')" type="info" show-icon :closable="false" class="note">
      <template #icon><el-icon><Lock /></el-icon></template>
    </el-alert>

    <CardFindPanel />

    <div class="panel block">
      <div class="head">
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
        <el-button v-if="selected" type="primary" :icon="Upload" @click="importOpen = true">{{ t('inventory.importTitle') }}</el-button>
      </div>
      <div class="muted small hint">{{ t('inventory.onlyAuto') }}</div>
    </div>

    <template v-if="selected">
      <div class="panel block">
        <h2 class="h2">{{ t('cards.batches') }}</h2>
        <el-table :data="batches" size="small" :empty-text="t('common.empty')" :row-class-name="({ row }) => (row.id === filters.batch_id ? 'sel-row' : '')">
          <el-table-column :label="t('cards.col.note')" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.note || t('cards.noNote') }}</template>
          </el-table-column>
          <el-table-column :label="t('common.createdAt')" width="150">
            <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
          </el-table-column>
          <el-table-column :label="t('cards.col.expires')" width="150">
            <template #default="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at, locale) : '—' }}</template>
          </el-table-column>
          <el-table-column :label="t('inventory.cardStatus.available')" prop="available" width="90" />
          <el-table-column :label="t('inventory.cardStatus.sold')" prop="sold" width="80" />
          <el-table-column :label="t('inventory.cardStatus.void')" prop="void" width="80" />
          <el-table-column :label="t('common.actions')" width="230">
            <template #default="{ row }">
              <el-button link type="primary" @click="viewBatch(row as CardBatch)">{{ filters.batch_id === row.id ? t('cards.clearFilter') : t('cards.viewCards') }}</el-button>
              <el-button link type="danger" :disabled="(row as CardBatch).available + ((row as CardBatch).reserved ?? 0) === 0" @click="askVoidBatch(row as CardBatch)">{{ t('cards.voidRemaining') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="panel">
        <div class="page-head">
          <h2 class="h2">{{ t('inventory.cards') }}</h2>
          <div class="filters">
            <el-select v-model="filters.status" style="width: 140px" :aria-label="t('common.status')">
              <el-option value="" :label="t('inventory.allStatus')" />
              <el-option v-for="s in CARD_STATUSES" :key="s" :value="s" :label="t(`inventory.cardStatus.${s}`)" />
            </el-select>
            <el-select v-if="variants.length" v-model="filters.variant_id" clearable style="width: 180px" :placeholder="t('variants.allVariants')" :aria-label="t('variants.title')">
              <el-option v-for="v in variants" :key="v.id" :value="v.id" :label="variantName(v)" />
            </el-select>
            <el-select v-model="filters.batch_id" clearable style="width: 200px" :placeholder="t('cards.allBatches')" :aria-label="t('cards.col.batch')">
              <el-option v-for="b in batches" :key="b.id" :value="b.id" :label="batchLabel(b)" />
            </el-select>
            <el-select v-model="filters.expiring_days" clearable style="width: 160px" :placeholder="t('cards.expiringFilter')" :aria-label="t('cards.expiringFilter')">
              <el-option v-for="d in [3, 7, 14, 30]" :key="d" :value="d" :label="t('cards.expiringWithin', { n: d })" />
            </el-select>
            <el-input v-model="filters.order_no" clearable style="width: 180px" :placeholder="t('cards.orderNo')" :aria-label="t('cards.orderNo')" />
            <el-button type="danger" plain :disabled="!voidable.length" @click="askVoidSelected">{{ t('cards.voidSelected', { n: voidable.length }) }}</el-button>
          </div>
        </div>
        <el-table v-loading="cards.loading.value" :data="cards.items.value" row-key="id" :empty-text="t('common.empty')" @selection-change="(v: CardRowV3[]) => (selection = v)">
          <el-table-column type="selection" width="44" :selectable="(r: CardRowV3) => canVoid(r.status)" />
          <el-table-column :label="t('inventory.masked')" min-width="200">
            <template #default="{ row }"><span class="mono">{{ row.masked }}</span></template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'available' ? 'success' : row.status === 'void' ? 'danger' : 'info'">{{ t(`inventory.cardStatus.${row.status}`) }}</el-tag>
              <div v-if="row.void_reason" class="muted small">{{ row.void_reason }}</div>
            </template>
          </el-table-column>
          <el-table-column v-if="variants.length" :label="t('variants.title')" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ variantLabel((row as CardRowV3).variant_id) }}</template>
          </el-table-column>
          <el-table-column :label="t('cards.col.batch')" width="100">
            <template #default="{ row }"><span class="mono small">{{ row.batch_id ? row.batch_id.slice(0, 6) : '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('cards.col.expires')" width="170">
            <template #default="{ row }">
              <template v-if="row.expires_at">
                {{ formatDateTime(row.expires_at, locale) }}
                <el-tag v-if="canVoid(row.status) && (daysUntil(row.expires_at) ?? 99) <= 7" size="small" type="warning">{{ t('cards.expiringIn', { n: Math.max(0, daysUntil(row.expires_at) ?? 0) }) }}</el-tag>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('inventory.order')" min-width="150">
            <template #default="{ row }">
              <RouterLink v-if="row.order_no || row.order_id" :to="orderLink(row as CardRowV3)" class="mono small">{{ row.order_no ?? row.order_id }}</RouterLink>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.createdAt')" width="150">
            <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="130" fixed="right">
            <template #default="{ row }">
              <el-button v-if="canVoid(row.status)" link type="primary" @click="askReplace(row as CardRowV3)">{{ t('cards.replace.title') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination layout="total, prev, pager, next" :total="cards.total.value" :page-size="cards.pageSize" :current-page="cards.page.value" @current-change="cards.load" />
        </div>
      </div>

      <CardImportDialog v-model="importOpen" :product-id="selected.id" :product-title="titleOf(selected)" @imported="refresh" />
    </template>
    <CardReasonDialog v-model="voidDlg.open" :title="t('cards.void')" :message="voidMessage" :busy="voidDlg.busy" @confirm="doVoid" />
    <CardReplaceDialog v-model="replaceDlg.open" :card-id="replaceDlg.id" :masked="replaceDlg.masked" @done="refresh" />
  </div>
</template>

<style scoped>
.note,
.block {
  margin-bottom: 16px;
}
.head {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  flex-wrap: wrap;
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
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
:deep(.sel-row) {
  --el-table-tr-bg-color: var(--el-color-primary-light-9);
}
</style>
