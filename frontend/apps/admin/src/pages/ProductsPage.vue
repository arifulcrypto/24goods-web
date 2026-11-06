<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, Search } from '@element-plus/icons-vue'
import { formatDateTime, formatMoney, type Category, type ProductStatus } from '@24goods/shared'
import { ProductThumb } from '@24goods/shared/ui'
import { api, apiV3 } from '../api'
import type { AdminProductV3 as AdminProduct } from '../api-v3'
import ProductDialog from '../components/ProductDialog.vue'
import { usePaged } from '../composables/usePaged'
import { useAuthStore } from '../stores/auth'
import { confirmBox, toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const auth = useAuthStore()
const q = ref('')
const status = ref<ProductStatus | ''>('')
const categories = ref<Category[]>([])
const dialog = ref(false)
const editing = ref<AdminProduct | null>(null)

const table = usePaged<AdminProduct>((page, page_size) =>
  apiV3.products.list({ q: q.value.trim() || undefined, status: status.value, page, page_size }),
)
let debounce: ReturnType<typeof setTimeout> | null = null
watch(q, () => {
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => void table.reload(), 300)
})
watch(status, () => void table.reload())

onMounted(async () => {
  void table.load()
  try {
    categories.value = await api.categories()
  } catch {
    /* optional */
  }
})

const catName = (key: string) => categories.value.find((c) => c.key === key)?.name ?? key
const titleOf = (p: AdminProduct) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en

function openNew() {
  editing.value = null
  dialog.value = true
}
function openEdit(p: AdminProduct) {
  editing.value = p
  dialog.value = true
}
function onSaved(p: AdminProduct) {
  if (table.items.value.some((x) => x.id === p.id)) table.patch((x) => x.id === p.id, p)
  else void table.reload()
}
async function unpublish(p: AdminProduct) {
  if (!(await confirmBox(t('products.deleteConfirm', { title: titleOf(p) })))) return
  try {
    await api.products.remove(p.id)
    toastOk(t('products.deleted'))
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
        </el-select>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" :icon="Plus" @click="openNew">{{ t('products.new') }}</el-button>
      <el-text v-else type="info" size="small">{{ t('products.readOnly') }}</el-text>
    </div>

    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('products.col.title')" min-width="260">
          <template #default="{ row }">
            <div class="prod">
              <ProductThumb :src="row.cover" :title="titleOf(row as AdminProduct)" :size="44" :radius="6" class="thumb" />
              <div class="prod-text">
                <div class="prod-title">{{ titleOf(row as AdminProduct) }}</div>
                <div class="muted small">{{ catName(row.category) }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.price')" width="120">
          <template #default="{ row }"><span class="price">{{ formatMoney(row.price, row.currency, locale) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('products.col.floor')" width="120">
          <template #default="{ row }">{{ formatMoney(row.floor_price, row.currency, locale) }}</template>
        </el-table-column>
        <el-table-column :label="t('products.col.mode')" width="110">
          <template #default="{ row }">
            <el-tag :type="row.delivery_mode === 'auto_card' ? 'warning' : 'info'" size="small">{{ t(`deliveryMode.${row.delivery_mode}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.stock')" width="90">
          <template #default="{ row }">{{ row.stock ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('products.col.sold')" prop="sold" width="80" />
        <el-table-column :label="t('products.col.status')" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">{{ t(`products.status.${row.status}`) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('products.col.created')" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" :label="t('common.actions')" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row as AdminProduct)">{{ t('common.edit') }}</el-button>
            <el-button v-if="row.status === 'active'" link type="danger" @click="unpublish(row as AdminProduct)">{{ t('products.toDraft') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination
          layout="total, prev, pager, next"
          :total="table.total.value"
          :page-size="table.pageSize"
          :current-page="table.page.value"
          @current-change="table.load"
        />
      </div>
    </div>

    <ProductDialog v-model="dialog" :product="editing" :categories="categories" @saved="onSaved" />
  </div>
</template>

<style scoped>
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
.thumb.ph {
  background: #f3f4f6;
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
</style>
