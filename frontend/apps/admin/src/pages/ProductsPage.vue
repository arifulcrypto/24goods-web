<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, Rank, Search, Upload, Top, Bottom } from '@element-plus/icons-vue'
import { CATALOG_SORTS, copyText, formatDateTime, formatMoney, isApiError, type CatalogSort, type Category } from '@24goods/shared'
import { ProductThumb } from '@24goods/shared/ui'
import { api, apiV5 } from '../api'
import type { AdminProductV5 as AdminProduct, BulkAction, ProductPrefill } from '../api-v5'
import ImportDialog from '../components/ImportDialog.vue'
import ProductDialog from '../components/ProductDialog.vue'
import { usePaged } from '../composables/usePaged'
import { moved, useRowReorder } from '../composables/useRowReorder'
import { marginMinor, yuanToMinor } from '../lib/money'
import { useAuthStore } from '../stores/auth'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const auth = useAuthStore()
const q = ref('')
const status = ref<'' | 'active' | 'draft' | 'archived'>('')
const categories = ref<Category[]>([])
const dialog = ref(false)
const editing = ref<AdminProduct | null>(null)
const prefill = ref<ProductPrefill | null>(null)
const importOpen = ref(false)
const selected = ref<AdminProduct[]>([])
const catDialog = ref(false)
const bulkCat = ref('')
const defaultEta = ref(24)

const PAGE = 100
const table = usePaged<AdminProduct>((page, page_size) =>
  apiV5.products.list({ q: q.value.trim() || undefined, status: status.value || undefined, page, page_size }),
PAGE)

// v11 W3: pin + manual order (drag the grip or use the arrows). Reordering writes the whole list,
// so it is only offered when every product is on screen (no search / status filter, one page).
const canReorder = computed(() => auth.isAdmin && !q.value.trim() && !status.value && table.total.value <= PAGE)
const defaultSort = ref<CatalogSort>('manual')
async function applyOrder(next: AdminProduct[]) {
  const prev = table.items.value
  table.items.value = next
  try {
    await apiV5.products.reorder(next.map((p) => p.id))
    toastOk(t('catalogOrder.saved'))
    await table.load()
  } catch (e) {
    table.items.value = prev
    toastError(e)
  }
}
const reorder = useRowReorder((from, to) => void applyOrder(moved(table.items.value, from, to)))
async function togglePin(p: AdminProduct) {
  try {
    await apiV5.products.pin(p.id, !p.pinned)
    await table.load()
  } catch (e) {
    toastError(e)
  }
}
async function setDefaultSort(v: CatalogSort) {
  try {
    await apiV5.catalog.setDefaultSort(v)
    defaultSort.value = v
    toastOk(t('catalogOrder.sortSaved'))
  } catch (e) {
    toastError(e)
  }
}
let debounce: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void table.reload(), 300)
})
watch(status, () => void table.reload())

onMounted(async () => {
  void table.load()
  try {
    categories.value = (await apiV5.categories.list()).map((c) => ({ key: c.slug, name: (locale.value === 'zh-CN' && c.name_i18n['zh-CN']) || c.name_i18n.en }) as Category)
  } catch {
    try {
      categories.value = await api.categories()
    } catch {
      /* optional */
    }
  }
  try {
    defaultEta.value = (await apiV5.site.get()).delivery?.manual_eta_hours ?? 24
    defaultSort.value = (await apiV5.catalog.getSettings()).default_sort
  } catch {
    /* admin-only; keep 24 */
  }
})

const catName = (key: string) => categories.value.find((c) => c.key === key)?.name ?? key
const titleOf = (p: AdminProduct) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en
const marginOf = (p: AdminProduct) => marginMinor(yuanToMinor(p.price), p.cost_minor)
const isArchived = (p: AdminProduct) => p.status === 'archived'
const bulkOn = computed(() => auth.isAdmin && selected.value.length > 0)

async function copyLink(p: AdminProduct) {
  if (await copyText(`${location.origin}/buy/${p.id}`)) toastOk(t('products.linkCopied'))
  else toastError(new Error('copy'))
}
function openNew() {
  editing.value = null
  prefill.value = null
  dialog.value = true
}
function openEdit(p: AdminProduct) {
  editing.value = p
  prefill.value = null
  dialog.value = true
}
function onImported(d: ProductPrefill) {
  importOpen.value = false
  editing.value = null
  prefill.value = d
  dialog.value = true
}
function onSaved(p: AdminProduct) {
  if (table.items.value.some((x) => x.id === p.id)) table.patch((x) => x.id === p.id, p)
  else void table.reload()
}
async function archive(p: AdminProduct) {
  if (!(await confirmBox(t('v5.products.archiveConfirm', { title: titleOf(p) })))) return
  try {
    await apiV5.products.archive(p.id)
    toastOk(t('v5.products.archived'))
    void table.load()
  } catch (e) {
    toastError(e)
  }
}
async function restore(p: AdminProduct) {
  try {
    await apiV5.products.restore(p.id)
    toastOk(t('v5.products.restored'))
    void table.load()
  } catch (e) {
    toastError(e)
  }
}
async function hardDelete(p: AdminProduct) {
  if (!(await confirmBox(t('v5.products.hardConfirm', { title: titleOf(p) }), t('v5.products.hardTitle')))) return
  try {
    await apiV5.products.hardDelete(p.id)
    toastOk(t('v5.products.hardDeleted'))
    void table.load()
  } catch (e) {
    if (isApiError(e) && e.code === 'product_in_use') ElMessage.warning({ message: t('v5.products.inUse'), grouping: true })
    else toastError(e)
  }
}
async function bulk(action: BulkAction, category?: string) {
  const ids = selected.value.map((p) => p.id)
  if (!ids.length) return
  if (action === 'archive' && !(await confirmBox(t('v5.products.bulkArchiveConfirm', { n: ids.length })))) return
  try {
    const r = await apiV5.products.bulk({ ids, action, ...(category ? { category } : {}) })
    toastOk(t('v5.products.bulkDone', { n: r.updated }))
    catDialog.value = false
    selected.value = []
    void table.load()
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="toolbar">
        <el-input v-model="q" :placeholder="t('products.search')" :prefix-icon="Search" clearable style="width: 260px" />
        <el-select v-model="status" style="width: 160px">
          <el-option value="" :label="t('products.allStatus')" />
          <el-option value="active" :label="t('products.status.active')" />
          <el-option value="draft" :label="t('products.status.draft')" />
          <el-option value="archived" :label="t('v5.products.statusArchived')" />
        </el-select>
      </div>
      <div v-if="auth.isAdmin" class="toolbar">
        <el-select :model-value="defaultSort" style="width: 190px" :aria-label="t('catalogOrder.defaultSort')" @change="setDefaultSort">
          <el-option v-for="o in CATALOG_SORTS" :key="o" :value="o" :label="t('catalogOrder.defaultSortValue', { sort: t(`catalogOrder.sort.${o}`) })" />
        </el-select>
        <el-button :icon="Upload" @click="importOpen = true">{{ t('v5.products.import') }}</el-button>
        <el-button type="primary" :icon="Plus" @click="openNew">{{ t('products.new') }}</el-button>
      </div>
      <el-text v-else type="info" size="small">{{ t('products.readOnly') }}</el-text>
    </div>

    <div v-if="bulkOn" class="bulkbar panel" role="toolbar" :aria-label="t('v5.products.bulk')">
      <strong>{{ t('v5.products.selected', { n: selected.length }) }}</strong>
      <el-button size="small" @click="bulk('publish')">{{ t('v5.products.publish') }}</el-button>
      <el-button size="small" @click="bulk('unpublish')">{{ t('v5.products.unpublish') }}</el-button>
      <el-button size="small" @click="catDialog = true">{{ t('v5.products.setCategory') }}</el-button>
      <el-button size="small" type="danger" plain @click="bulk('archive')">{{ t('v5.products.archive') }}</el-button>
      <el-button size="small" @click="bulk('restore')">{{ t('v5.products.restore') }}</el-button>
      <el-button size="small" link @click="selected = []">{{ t('v5.products.clearSel') }}</el-button>
    </div>

    <p v-if="auth.isAdmin" class="muted small hint">{{ canReorder ? t('catalogOrder.hint') : t('catalogOrder.hintOff') }}</p>
    <div class="panel" @dragover.prevent @drop="reorder.drop">
      <el-table
        v-loading="table.loading.value"
        :data="table.items.value"
        row-key="id"
        :empty-text="t('common.empty')"
        @selection-change="selected = $event"
      >
        <el-table-column v-if="auth.isAdmin" type="selection" width="44" reserve-selection />
        <el-table-column v-if="canReorder" :label="t('catalogOrder.order')" width="110">
          <template #default="{ $index, row }">
            <span class="grip" draggable="true" role="img" :aria-label="t('catalogOrder.drag')" @dragstart="reorder.start($index, $event)"><el-icon><Rank /></el-icon></span>
            <el-button link :icon="Top" :disabled="$index === 0 || table.items.value[$index - 1]?.pinned !== row.pinned" :aria-label="t('catalogOrder.up')" @click="reorder.move($index, -1)" />
            <el-button link :icon="Bottom" :disabled="$index === table.items.value.length - 1 || table.items.value[$index + 1]?.pinned !== row.pinned" :aria-label="t('catalogOrder.down')" @click="reorder.move($index, 1)" />
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.title')" min-width="260">
          <template #default="{ row }">
            <div class="prod">
              <ProductThumb :src="row.cover" :title="titleOf(row as AdminProduct)" :size="44" :radius="6" class="thumb" />
              <div class="prod-text">
                <div class="prod-title"><el-tag v-if="row.pinned" size="small" type="warning" class="vtag">{{ t('catalogOrder.pinned') }}</el-tag>{{ titleOf(row as AdminProduct) }}</div>
                <div class="muted small">{{ catName(row.category) }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.price')" width="120">
          <template #default="{ row }">
            <span class="price">{{ formatMoney(row.price, row.currency, locale) }}</span>
            <el-tag v-if="row.variants?.some((v: { status: string }) => v.status === 'active')" size="small" type="info" class="vtag">{{ t('variants.count', row.variants.filter((v: { status: string }) => v.status === 'active').length) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" :label="t('v5.product.margin')" width="110">
          <template #default="{ row }">
            <span v-if="marginOf(row as AdminProduct) !== null" :class="(marginOf(row as AdminProduct) as number) < 0 ? 'neg' : 'pos'">
              {{ formatMoney(((marginOf(row as AdminProduct) as number) / 100).toFixed(2), row.currency, locale) }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.floor')" width="110">
          <template #default="{ row }">{{ formatMoney(row.floor_price, row.currency, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('products.col.mode')" width="110">
          <template #default="{ row }">
            <el-tag :type="row.delivery_mode === 'auto_card' ? 'warning' : 'info'" size="small">{{ t(`deliveryMode.${row.delivery_mode}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.stock')" width="80">
          <template #default="{ row }">{{ row.stock ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('products.col.sold')" prop="sold" width="70" />
        <el-table-column :label="t('products.col.status')" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">{{ t(row.status === 'archived' ? 'v5.products.statusArchived' : `products.status.${row.status}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.created')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="320" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="copyLink(row as AdminProduct)">{{ t('products.copyLink') }}</el-button>
            <template v-if="auth.isAdmin">
              <el-button link type="primary" @click="togglePin(row as AdminProduct)">{{ row.pinned ? t('catalogOrder.unpin') : t('catalogOrder.pin') }}</el-button>
              <el-button link type="primary" @click="openEdit(row as AdminProduct)">{{ t('common.edit') }}</el-button>
              <template v-if="isArchived(row as AdminProduct)">
                <el-button link type="primary" @click="restore(row as AdminProduct)">{{ t('v5.products.restore') }}</el-button>
                <el-button link type="danger" @click="hardDelete(row as AdminProduct)">{{ t('v5.products.hardDelete') }}</el-button>
              </template>
              <el-button v-else link type="danger" @click="archive(row as AdminProduct)">{{ t('v5.products.archive') }}</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" />
      </div>
    </div>

    <ProductDialog v-model="dialog" :product="editing" :categories="categories" :prefill="prefill" :default-eta-hours="defaultEta" @saved="onSaved" />
    <ImportDialog v-model="importOpen" @create="onImported" />
    <el-dialog v-model="catDialog" :title="t('v5.products.setCategory')" width="min(380px, 96vw)">
      <el-select v-model="bulkCat" filterable style="width: 100%" :placeholder="t('products.form.category')">
        <el-option v-for="c in categories" :key="c.key" :label="c.name" :value="c.key" />
      </el-select>
      <template #footer>
        <el-button @click="catDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!bulkCat" @click="bulk('set_category', bulkCat)">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.vtag {
  margin-left: 6px;
}
.grip {
  cursor: grab;
  padding: 0 6px;
  color: var(--el-text-color-secondary);
}
.hint {
  margin: 0 0 8px;
}
.prod {
  display: flex;
  align-items: center;
  gap: 10px;
}
.thumb {
  width: 44px;
  height: 44px;
  border-radius: 6px;
  flex: none;
}
.prod-text {
  min-width: 0;
}
.prod-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.small {
  font-size: 12px;
}
.bulkbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  padding: 10px 14px;
}
.pos {
  color: var(--el-color-success);
}
.neg {
  color: var(--el-color-danger);
}
</style>
