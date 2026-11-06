<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  amountToMinor,
  fromMinor,
  goBack,
  markedUpPrice,
  markupAmount,
  percentToPermille,
  permilleToPercent,
  type MarkupKind,
  type ResellerProduct,
} from '@24goods/shared'
import { api } from '../../api'
import { minorMoney } from '../../stores/reseller'
import { showSuccessToast, toastError } from '../../ui'

const { t, locale } = useI18n()
const router = useRouter()
const items = ref<ResellerProduct[]>([])
const q = ref('')
const page = ref(1)
const loading = ref(false)
const finished = ref(false)
const error = ref(false)
const PAGE = 20

async function load() {
  try {
    const r = await api.reseller.products({ q: q.value.trim() || undefined, page: page.value, page_size: PAGE })
    items.value.push(...r.items)
    finished.value = items.value.length >= r.total || r.items.length < PAGE
    page.value++
  } catch (e) {
    error.value = true
    toastError(e)
  } finally {
    loading.value = false
  }
}
function search() {
  items.value = []
  page.value = 1
  finished.value = false
  loading.value = true
  void load()
}

const money = (n: number, c: string) => minorMoney(n, c, locale.value)
function markupLabel(p: ResellerProduct): string {
  return p.markup_kind === 'percent' ? `+${permilleToPercent(p.markup_value)}%` : `+${money(p.markup_value, p.currency)}`
}

// ---- edit sheet ----
const show = ref(false)
const cur = ref<ResellerProduct | null>(null)
const kind = ref<MarkupKind>('percent')
const val = ref('')
const saving = ref(false)
function edit(p: ResellerProduct) {
  cur.value = p
  kind.value = p.markup_kind
  val.value = p.markup_kind === 'percent' ? permilleToPercent(p.markup_value) : fromMinor(BigInt(p.markup_value)).replace(/\.00$/, '')
  show.value = true
}
const parsed = computed<number | null>(() => (kind.value === 'percent' ? percentToPermille(val.value) : amountToMinor(val.value)))
const previewPrice = computed(() => (cur.value && parsed.value !== null ? markedUpPrice(cur.value.base_price_minor, kind.value, parsed.value) : null))
const previewMarkup = computed(() => (cur.value && parsed.value !== null ? markupAmount(cur.value.base_price_minor, kind.value, parsed.value) : null))

function replace(n: ResellerProduct) {
  const i = items.value.findIndex((x) => x.product_id === n.product_id)
  if (i >= 0) items.value[i] = n
}
async function save() {
  if (!cur.value || parsed.value === null || saving.value) return
  saving.value = true
  try {
    replace(await api.reseller.setProduct(cur.value.product_id, { markup_kind: kind.value, markup_value: parsed.value }))
    show.value = false
    showSuccessToast(t('common.saved'))
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function resetIt() {
  if (!cur.value || saving.value) return
  saving.value = true
  try {
    await api.reseller.resetProduct(cur.value.product_id)
    show.value = false
    search() // reload: the price falls back to the store default
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}
async function toggleHidden(p: ResellerProduct, hidden: boolean) {
  try {
    replace(await api.reseller.setProduct(p.product_id, { hidden }))
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="page">
    <van-nav-bar :title="t('reseller.products')" left-arrow fixed placeholder safe-area-inset-top class="dock-top" @click-left="goBack(router, '/reseller')" />
    <van-search v-model="q" :placeholder="t('reseller.searchProducts')" shape="round" @search="search" @clear="search" />
    <van-list v-model:loading="loading" v-model:error="error" :finished="finished" :finished-text="items.length ? t('common.noMore') : ''" :error-text="t('home.loadFailed')" @load="load">
      <div v-for="p in items" :key="p.product_id" class="card row" :class="{ hid: p.hidden }">
        <div class="head">
          <div class="title">{{ p.title }}</div>
          <van-switch :model-value="!p.hidden" size="22" :aria-label="t('reseller.visible')" @update:model-value="toggleHidden(p, !$event)" />
        </div>
        <div class="nums">
          <span class="muted">{{ t('reseller.basePrice') }} {{ money(p.base_price_minor, p.currency) }}</span>
          <span class="muted">{{ t('reseller.markup') }} {{ markupLabel(p) }}<template v-if="!p.custom"> ({{ t('reseller.default') }})</template></span>
          <span class="price">{{ money(p.price_minor, p.currency) }}</span>
        </div>
        <van-button size="small" round plain class="tap" @click="edit(p)">{{ t('reseller.editMarkup') }}</van-button>
      </div>
    </van-list>

    <van-popup v-model:show="show" position="bottom" round closeable safe-area-inset-bottom teleport="body" class="buy-sheet">
      <div v-if="cur" class="sheet">
        <h2>{{ t('reseller.editMarkup') }}</h2>
        <div class="muted small">{{ cur.title }}</div>
        <van-radio-group v-model="kind" direction="horizontal" class="kind">
          <van-radio name="percent">{{ t('reseller.kindPercent') }}</van-radio>
          <van-radio name="fixed">{{ t('reseller.kindFixed') }}</van-radio>
        </van-radio-group>
        <van-field v-model="val" inputmode="decimal" :label="kind === 'percent' ? '%' : '¥'" :error-message="parsed === null ? t('reseller.invalidNumber') : ''" />
        <div v-if="previewPrice !== null && previewMarkup !== null" class="prev">
          <span class="muted">{{ t('reseller.basePrice') }} {{ money(cur.base_price_minor, cur.currency) }} + {{ money(previewMarkup, cur.currency) }}</span>
          <strong class="price">{{ money(previewPrice, cur.currency) }}</strong>
        </div>
        <van-button block round type="primary" size="large" :loading="saving" :disabled="parsed === null" @click="save">{{ t('common.save') }}</van-button>
        <van-button v-if="cur.custom" block round plain size="large" class="reset" :disabled="saving" @click="resetIt">{{ t('reseller.resetDefault') }}</van-button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.row {
  margin: 8px 12px;
  padding: 12px;
}
.row.hid {
  opacity: 0.6;
}
.head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}
.title {
  font-weight: 600;
  font-size: 14px;
  overflow-wrap: anywhere;
}
.nums {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: baseline;
  margin: 6px 0 8px;
  font-size: 13px;
}
.sheet {
  padding: 20px 16px 16px;
}
.sheet h2 {
  font-size: 18px;
  margin: 0 0 4px;
}
.kind {
  padding: 12px 0;
}
.prev {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  padding: 8px 0 12px;
  font-size: 13px;
}
.reset {
  margin-top: 8px;
}
.small {
  font-size: 12px;
}
</style>
