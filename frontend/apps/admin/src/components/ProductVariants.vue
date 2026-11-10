<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  compareAmount,
  isValidAmount,
  normalizeAmount,
  type AdminVariant,
  type AdminVariantInput,
  type DeliveryMode,
} from '@24goods/shared'
import { api } from '../api'
import { confirmBox, toastError, toastOk } from '../ui'

/**
 * v11 W1: variants (SKUs) of an existing product. Each change is saved at once through the
 * variants API (price changes are audited server-side). NULL after-sales / subscription = inherit.
 */
const props = defineProps<{ productId: string; deliveryMode: DeliveryMode; currency: string }>()
const emit = defineEmits<{ changed: [] }>()
const { t, locale } = useI18n()

const items = ref<AdminVariant[]>([])
const loading = ref(false)
const saving = ref(false)
const dlg = ref(false)
const editingId = ref<string | null>(null)
type AsMode = 'inherit' | 'hours' | 'months'
const DEFAULT_SUB = { period_policy: 'calendar_month', period_days: 30, grace_days: 0, email_reminders: true, telegram_reminders: true }
const form = reactive({
  name_en: '',
  name_zh: '',
  price: '',
  floor_price: '0',
  manualStock: null as number | null,
  status: 'active' as 'active' | 'archived',
  asMode: 'inherit' as AsMode,
  asValue: 1,
  subscription: false,
})
let keptSub: Record<string, unknown> | null = null
const errors = ref<Record<string, string>>({})

const nameOf = (v: AdminVariant) => (locale.value === 'zh-CN' && v.name_i18n['zh-CN']) || v.name_i18n.en
const isManual = computed(() => props.deliveryMode === 'manual')

async function load() {
  loading.value = true
  try {
    items.value = (await api.products.variants.list(props.productId)).items
  } catch (e) {
    toastError(e)
  } finally {
    loading.value = false
  }
}
watch(() => props.productId, () => void load(), { immediate: true })

function openNew() {
  editingId.value = null
  Object.assign(form, { name_en: '', name_zh: '', price: '', floor_price: '0', manualStock: isManual.value ? 1 : null, status: 'active', asMode: 'inherit', asValue: 1, subscription: false })
  keptSub = null
  errors.value = {}
  dlg.value = true
}
function openEdit(v: AdminVariant) {
  editingId.value = v.id
  Object.assign(form, {
    name_en: v.name_i18n.en,
    name_zh: v.name_i18n['zh-CN'],
    price: v.price,
    floor_price: v.floor_price,
    manualStock: v.manual_stock,
    status: v.status,
    asMode: v.after_sales_hours !== null ? 'hours' : v.after_sales_months !== null ? 'months' : 'inherit',
    asValue: v.after_sales_hours ?? v.after_sales_months ?? 1,
    subscription: v.subscription !== null,
  })
  keptSub = v.subscription
  errors.value = {}
  dlg.value = true
}

function validate(): boolean {
  const e: Record<string, string> = {}
  if (!form.name_en.trim()) e.name = t('variants.v.nameRequired')
  if (!isValidAmount(form.price)) e.price = t('products.v.priceInvalid')
  if (!isValidAmount(form.floor_price) && form.floor_price.trim() !== '0') e.floor = t('products.v.priceInvalid')
  else if (!e.price && compareAmount(form.floor_price, form.price) === 1) e.floor = t('products.v.floorAbovePrice')
  errors.value = e
  return Object.keys(e).length === 0
}

function body(): AdminVariantInput {
  return {
    name_i18n: { en: form.name_en.trim(), 'zh-CN': form.name_zh.trim() },
    price: normalizeAmount(form.price)!,
    floor_price: form.floor_price.trim() === '0' ? '0' : normalizeAmount(form.floor_price)!,
    ...(isManual.value ? { manual_stock: form.manualStock } : {}),
    status: form.status,
    after_sales_hours: form.asMode === 'hours' ? form.asValue : null,
    after_sales_months: form.asMode === 'months' ? form.asValue : null,
    subscription: form.subscription ? (keptSub ?? DEFAULT_SUB) : null,
  }
}

async function save() {
  if (saving.value || !validate()) return
  saving.value = true
  try {
    if (editingId.value) await api.products.variants.update(props.productId, editingId.value, body())
    else await api.products.variants.create(props.productId, { ...body(), sort: items.value.length })
    dlg.value = false
    toastOk(t('common.saved'))
    await load()
    emit('changed')
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}

async function archive(v: AdminVariant) {
  if (!(await confirmBox(t('variants.confirmRemove', { name: nameOf(v) })))) return
  try {
    const r = await api.products.variants.remove(props.productId, v.id)
    toastOk(r.outcome === 'archived' ? t('variants.archived') : t('variants.deleted'))
    await load()
    emit('changed')
  } catch (e) {
    toastError(e)
  }
}
async function restore(v: AdminVariant) {
  try {
    await api.products.variants.update(props.productId, v.id, { status: 'active' })
    await load()
    emit('changed')
  } catch (e) {
    toastError(e)
  }
}
/** Swap with the neighbour and rewrite the sort values of the rows whose position changed. */
async function move(i: number, dir: -1 | 1) {
  const j = i + dir
  if (j < 0 || j >= items.value.length) return
  const next = items.value.slice()
  const [a] = next.splice(i, 1)
  next.splice(j, 0, a!)
  try {
    await Promise.all(next.map((v, idx) => (v.sort !== idx ? api.products.variants.update(props.productId, v.id, { sort: idx }) : null)))
    await load()
    emit('changed')
  } catch (e) {
    toastError(e)
  }
}
const termsText = (v: AdminVariant): string => {
  const parts: string[] = []
  if (v.after_sales_months !== null) parts.push(t('variants.asMonths', { n: v.after_sales_months }))
  else if (v.after_sales_hours !== null) parts.push(t('variants.asHours', { n: v.after_sales_hours }))
  if (v.subscription !== null) parts.push(t('variants.subscription'))
  return parts.join(' · ') || t('variants.inherit')
}
</script>

<template>
  <div class="variants">
    <div class="head">
      <h3 class="sec">{{ t('variants.title') }}</h3>
      <el-button size="small" type="primary" @click="openNew">{{ t('variants.add') }}</el-button>
    </div>
    <p class="muted hint">{{ t('variants.hint') }}</p>
    <el-table v-loading="loading" :data="items" size="small" :empty-text="t('variants.empty')">
      <el-table-column :label="t('variants.name')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          {{ nameOf(row as AdminVariant) }}
          <el-tag v-if="(row as AdminVariant).status === 'archived'" size="small" type="info">{{ t('variants.archivedTag') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="`${t('products.form.price')} (${currency})`" prop="price" width="110" />
      <el-table-column :label="t('variants.stock')" width="90">
        <template #default="{ row }">{{ (row as AdminVariant).stock ?? t('v5.product.unlimited') }}</template>
      </el-table-column>
      <el-table-column :label="t('variants.terms')" min-width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ termsText(row as AdminVariant) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="230">
        <template #default="{ row, $index }">
          <el-button link :disabled="$index === 0" :aria-label="t('variants.up')" @click="move($index, -1)">↑</el-button>
          <el-button link :disabled="$index === items.length - 1" :aria-label="t('variants.down')" @click="move($index, 1)">↓</el-button>
          <el-button link type="primary" @click="openEdit(row as AdminVariant)">{{ t('common.edit') }}</el-button>
          <el-button v-if="(row as AdminVariant).status === 'archived'" link type="primary" @click="restore(row as AdminVariant)">{{ t('variants.restore') }}</el-button>
          <el-button v-else link type="danger" @click="archive(row as AdminVariant)">{{ t('variants.remove') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="editingId ? t('variants.edit') : t('variants.add')" width="min(560px, 96vw)" append-to-body destroy-on-close>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item :label="`${t('variants.name')} (EN)`" :error="errors.name" required>
          <el-input v-model="form.name_en" maxlength="120" />
        </el-form-item>
        <el-form-item :label="`${t('variants.name')} (中文)`">
          <el-input v-model="form.name_zh" maxlength="120" />
        </el-form-item>
        <div class="grid">
          <el-form-item :label="`${t('products.form.price')} (${currency})`" :error="errors.price" required>
            <el-input v-model="form.price" inputmode="decimal" placeholder="19.90" />
          </el-form-item>
          <el-form-item :label="`${t('products.form.floorPrice')} (${currency})`" :error="errors.floor">
            <el-input v-model="form.floor_price" inputmode="decimal" placeholder="15.00" />
          </el-form-item>
        </div>
        <el-form-item v-if="isManual" :label="t('v5.product.manualStock')">
          <el-input-number v-model="form.manualStock" :min="0" :max="1000000" :placeholder="t('v5.product.unlimited')" />
          <div class="hint muted">{{ t('v5.product.manualStockHint') }}</div>
        </el-form-item>
        <p v-else class="hint muted">{{ t('variants.cardStockHint') }}</p>
        <el-form-item :label="t('delivery.afterSales')">
          <el-select v-model="form.asMode" style="width: 180px">
            <el-option value="inherit" :label="t('variants.inherit')" />
            <el-option value="hours" :label="t('variants.asHoursOpt')" />
            <el-option value="months" :label="t('variants.asMonthsOpt')" />
          </el-select>
          <el-input-number v-if="form.asMode !== 'inherit'" v-model="form.asValue" :min="form.asMode === 'hours' ? 0 : 1" :max="form.asMode === 'hours' ? 8760 : 12" class="as-num" />
        </el-form-item>
        <el-form-item :label="t('variants.subscription')">
          <el-switch v-model="form.subscription" />
          <span class="hint muted sw">{{ t('variants.subscriptionHint') }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.variants {
  margin: 8px 0 16px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.sec {
  margin: 0;
}
.hint {
  font-size: 12px;
  margin: 4px 0 8px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
.as-num {
  margin-left: 8px;
}
.sw {
  margin-left: 8px;
}
</style>
