<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus } from '@element-plus/icons-vue'
import { formatDateTime, isApiError } from '@24goods/shared'
import { apiV5 } from '../api'
import type { AdminCategory, AdminProductV5, AdminPromo, PromoRedemption } from '../api-v5'
import { usePaged } from '../composables/usePaged'
import { emptyPromoForm, formFromPromo, promoToInput, promoValueText, randomCode, type PromoForm } from '../lib/promo'
import { minorToYuan } from '../lib/money'
import { toastError, toastOk } from '../ui'

const { t, locale } = useI18n()
const table = usePaged<AdminPromo>((page, page_size) => apiV5.promos.list({ page, page_size }))
const dialog = ref(false)
const editing = ref<AdminPromo | null>(null)
const saving = ref(false)
const f = ref<PromoForm>(emptyPromoForm())
const win = ref<[Date, Date] | null>(null)
const errors = ref<Record<string, string>>({})
const cats = ref<AdminCategory[]>([])
const prods = ref<AdminProductV5[]>([])
const titles = new Map<string, string>()
const drawer = ref(false)
const drawerPromo = ref<AdminPromo | null>(null)
const redemptions = usePaged<PromoRedemption>((page, page_size) =>
  drawerPromo.value ? apiV5.promos.redemptions(drawerPromo.value.id, { page, page_size }) : Promise.resolve({ items: [], total: 0, page: 1, page_size }),
)

const titleOf = (p: AdminProductV5) => (locale.value === 'zh-CN' && p.title_i18n['zh-CN']) || p.title_i18n.en
function remember(items: AdminProductV5[]) {
  for (const p of items) titles.set(p.id, titleOf(p))
  prods.value = [...new Map([...prods.value, ...items].map((p) => [p.id, p])).values()]
}
async function searchProducts(q: string) {
  try {
    remember((await apiV5.products.list({ q: q.trim() || undefined, page: 1, page_size: 50 })).items)
  } catch (e) {
    toastError(e)
  }
}
onMounted(async () => {
  void table.load()
  try {
    cats.value = await apiV5.categories.list()
  } catch {
    /* optional */
  }
})
async function openDialog(p: AdminPromo | null) {
  editing.value = p
  f.value = p ? formFromPromo(p) : emptyPromoForm()
  win.value = f.value.window ? [new Date(f.value.window[0]), new Date(f.value.window[1])] : null
  if (!p) f.value.code = randomCode()
  errors.value = {}
  dialog.value = true
  await searchProducts('')
}
async function save() {
  if (saving.value) return
  f.value.window = win.value ? [win.value[0].toISOString(), win.value[1].toISOString()] : null
  const r = promoToInput(f.value)
  if ('err' in r) {
    errors.value = { [r.err]: t(`v5.promo.err.${r.err}`) }
    return
  }
  errors.value = {}
  if (win.value && win.value[0] >= win.value[1]) {
    errors.value = { window: t('v5.promo.err.window') }
    return
  }
  saving.value = true
  try {
    if (editing.value) await apiV5.promos.update(editing.value.id, r.ok)
    else await apiV5.promos.create(r.ok)
    toastOk(t('v5.promo.saved'))
    dialog.value = false
    void table.load()
  } catch (e) {
    if (isApiError(e) && e.status === 409) errors.value = { code: t('v5.promo.err.taken') }
    else toastError(e)
  } finally {
    saving.value = false
  }
}
async function toggle(p: AdminPromo, v: boolean) {
  try {
    await apiV5.promos.update(p.id, { enabled: v })
    p.enabled = v
  } catch (e) {
    toastError(e)
  }
}
function openRedemptions(p: AdminPromo) {
  drawerPromo.value = p
  drawer.value = true
  void redemptions.reload()
}
const windowText = (p: AdminPromo) =>
  p.starts_at || p.ends_at ? `${p.starts_at ? formatDateTime(p.starts_at, locale.value) : '…'} → ${p.ends_at ? formatDateTime(p.ends_at, locale.value) : '…'}` : t('v5.promo.always')
</script>

<template>
  <div class="page">
    <div class="page-head">
      <span class="muted">{{ t('v5.promo.intro') }}</span>
      <el-button type="primary" :icon="Plus" @click="openDialog(null)">{{ t('v5.promo.new') }}</el-button>
    </div>
    <div class="panel">
      <el-table v-loading="table.loading.value" :data="table.items.value" row-key="id" :empty-text="t('common.empty')">
        <el-table-column :label="t('v5.promo.code')" width="170"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column :label="t('v5.promo.discount')" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'percent' ? 'warning' : 'success'">{{ promoValueText(row as AdminPromo) }}</el-tag>
            <div v-if="row.max_discount_minor != null" class="muted small">{{ t('v5.promo.capShort', { v: minorToYuan(row.max_discount_minor) }) }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('v5.promo.usage')" width="130">
          <template #default="{ row }"><a href="#" @click.prevent="openRedemptions(row as AdminPromo)">{{ row.used_count }} / {{ row.usage_limit ?? '∞' }}</a></template>
        </el-table-column>
        <el-table-column :label="t('v5.promo.window')" min-width="260"><template #default="{ row }">{{ windowText(row as AdminPromo) }}</template></el-table-column>
        <el-table-column :label="t('v5.promo.scope')" width="130">
          <template #default="{ row }">
            <span v-if="!row.product_ids.length && !row.category_slugs.length" class="muted">{{ t('v5.promo.all') }}</span>
            <span v-else>{{ t('v5.promo.scopeCount', { p: row.product_ids.length, c: row.category_slugs.length }) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('v5.promo.note')" prop="note" min-width="140" show-overflow-tooltip />
        <el-table-column :label="t('v5.enabled')" width="90">
          <template #default="{ row }"><el-switch :model-value="row.enabled" :aria-label="t('v5.enabled')" @change="(v: string | number | boolean) => toggle(row as AdminPromo, !!v)" /></template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDialog(row as AdminPromo)">{{ t('common.edit') }}</el-button>
            <el-button link type="primary" @click="openRedemptions(row as AdminPromo)">{{ t('v5.promo.redemptions') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager"><el-pagination layout="total, prev, pager, next" :total="table.total.value" :page-size="table.pageSize" :current-page="table.page.value" @current-change="table.load" /></div>
    </div>

    <el-dialog v-model="dialog" :title="editing ? t('v5.promo.edit') : t('v5.promo.new')" width="min(640px, 96vw)" destroy-on-close>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item :label="t('v5.promo.code')" :error="errors.code">
          <el-input v-model="f.code" maxlength="32" class="mono" spellcheck="false" style="text-transform: uppercase">
            <template #append><el-button @click="f.code = randomCode()">{{ t('v5.promo.random') }}</el-button></template>
          </el-input>
        </el-form-item>
        <div class="grid">
          <el-form-item :label="t('v5.promo.kind')">
            <el-radio-group v-model="f.kind" @change="f.value = f.kind === 'percent' ? '10' : '5.00'">
              <el-radio-button value="percent">{{ t('v5.promo.percent') }}</el-radio-button>
              <el-radio-button value="fixed">{{ t('v5.promo.fixed') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="f.kind === 'percent' ? t('v5.promo.valuePct') : t('v5.promo.valueYuan')" :error="errors.value">
            <el-input v-model="f.value" inputmode="decimal"><template #suffix>{{ f.kind === 'percent' ? '%' : '¥' }}</template></el-input>
          </el-form-item>
          <el-form-item :label="t('v5.promo.minSubtotal')" :error="errors.min"><el-input v-model="f.min_subtotal" inputmode="decimal" placeholder="0"><template #prefix>¥</template></el-input></el-form-item>
          <el-form-item :label="t('v5.promo.maxDiscount')" :error="errors.max"><el-input v-model="f.max_discount" inputmode="decimal" :placeholder="t('v5.promo.noCap')"><template #prefix>¥</template></el-input></el-form-item>
          <el-form-item :label="t('v5.promo.perUser')" :error="errors.limit"><el-input-number v-model="f.per_user_limit" :min="1" :max="1000" /></el-form-item>
          <el-form-item :label="t('v5.promo.usageLimit')">
            <el-input-number v-model="f.usage_limit" :min="1" :max="10000000" :placeholder="t('v5.promo.unlimited')" />
          </el-form-item>
        </div>
        <el-form-item :label="t('v5.promo.window')" :error="errors.window">
          <el-date-picker v-model="win" type="datetimerange" format="YYYY-MM-DD HH:mm" :start-placeholder="t('v5.promo.start')" :end-placeholder="t('v5.promo.end')" :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]" style="width: 100%" />
          <div class="muted small">{{ t('v5.promo.windowHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('v5.promo.products')">
          <el-select v-model="f.product_ids" multiple filterable remote reserve-keyword :remote-method="searchProducts" style="width: 100%" :placeholder="t('v5.promo.allProducts')">
            <el-option v-for="p in prods" :key="p.id" :value="p.id" :label="titleOf(p)" />
            <el-option v-for="id in f.product_ids.filter((i) => !prods.some((p) => p.id === i))" :key="id" :value="id" :label="titles.get(id) ?? id.slice(0, 8)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('v5.promo.categories')">
          <el-select v-model="f.category_slugs" multiple filterable style="width: 100%" :placeholder="t('v5.promo.allCategories')">
            <el-option v-for="c in cats" :key="c.slug" :value="c.slug" :label="(locale === 'zh-CN' && c.name_i18n['zh-CN']) || c.name_i18n.en" />
          </el-select>
          <div class="muted small">{{ t('v5.promo.scopeHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('v5.promo.note')"><el-input v-model="f.note" maxlength="200" show-word-limit /></el-form-item>
        <el-form-item :label="t('v5.enabled')"><el-switch v-model="f.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" :title="t('v5.promo.redemptionsOf', { code: drawerPromo?.code ?? '' })" size="min(560px, 96vw)">
      <el-table v-loading="redemptions.loading.value" :data="redemptions.items.value" size="small" :empty-text="t('common.empty')">
        <el-table-column :label="t('v5.promo.user')" prop="user" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('v5.promo.order')" min-width="150"><template #default="{ row }"><span class="mono">{{ row.order_no ?? '—' }}</span></template></el-table-column>
        <el-table-column :label="t('v5.promo.discount')" width="100"><template #default="{ row }">¥{{ minorToYuan(row.discount_minor) }}</template></el-table-column>
        <el-table-column :label="t('products.col.status')" width="100">
          <template #default="{ row }"><el-tag size="small" :type="row.status === 'used' ? 'success' : row.status === 'reserved' ? 'warning' : 'info'">{{ t(`v5.promo.rs.${row.status}`) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('audit.at')" width="160"><template #default="{ row }">{{ formatDateTime(row.created_at, locale) }}</template></el-table-column>
      </el-table>
      <div class="pager"><el-pagination layout="total, prev, pager, next" :total="redemptions.total.value" :page-size="redemptions.pageSize" :current-page="redemptions.page.value" @current-change="redemptions.load" /></div>
    </el-drawer>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  column-gap: 16px;
}
.small {
  font-size: 12px;
}
</style>
