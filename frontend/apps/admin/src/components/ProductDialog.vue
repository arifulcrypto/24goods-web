<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UploadRequestOptions } from 'element-plus'
import {
  compareAmount,
  compressImage,
  errorI18nKey,
  imageExtension,
  isAcceptedImage,
  isValidAmount,
  normalizeAmount,
  usePasteImage,
  type Category,
  type DeliveryMode,
  type ProductStatus,
} from '@24goods/shared'
import { api, apiV3, apiV5 } from '../api'
import { copyImages, draftToFields, pickClipboard } from '../lib/importDraft'
import { safeUrl } from '../lib/markdown'
import type { AdminProductInputV3, CardImportResultV3 } from '../api-v3'
import { BUYER_INPUT_DEFAULT, type BuyerInputForm } from '../api-v4'
import type { AdminProductV5 as AdminProduct, ProductPrefill } from '../api-v5'
import { marginMinor, marginPct, minorToYuan, yuanToMinor } from '../lib/money'
import MarkdownEditor from './MarkdownEditor.vue'
import { promptChips, validateBuyerInput } from '../lib/buyerInput'
import {
  AFTER_SALES_UNITS,
  DEFAULT_SEPARATOR,
  FORMAT_PRESETS,
  SEPARATORS,
  afterSalesFromForm,
  afterSalesToForm,
  afterSalesValid,
  formatLabels,
  isSeparator,
  isValidRedeemUrl,
  joinFormat,
  presetFor,
  splitCard,
  type AfterSalesUnit,
  type FormatPresetKey,
} from '../lib/delivery'
import { toastError, toastOk } from '../ui'

const MAX_AUTOFILL_IMAGES = 5
const afOpen = ref<string[]>([])
const afUrl = ref('')
const afText = ref('')
const afHtml = ref('')
const afBusy = ref(false)
const cardLines = ref('')
const cardResult = ref<CardImportResultV3 | null>(null)
const cardError = ref('')
const cardSummary = computed(() => (cardResult.value ? t('v5.autofill.cardsDone', { imported: cardResult.value.imported, duplicates: cardResult.value.duplicates, rejected: cardResult.value.rejected }) : ''))
const cardCount = computed(() => cardLines.value.split(/\r?\n/).filter((l) => l.trim()).length)

function onAfPaste(e: ClipboardEvent) {
  const c = pickClipboard(e.clipboardData)
  if (c.html) {
    e.preventDefault()
    afHtml.value = c.html.slice(0, 2_000_000)
    afText.value = (e.clipboardData?.getData('text/plain') ?? '').slice(0, 20000)
  } else afHtml.value = ''
}
function onAfInput(v: string) {
  afText.value = v
  afHtml.value = ''
}
async function autofill(kind: 'url' | 'paste') {
  if (afBusy.value) return
  const u = afUrl.value.trim()
  if (kind === 'url' ? !/^https?:\/\/\S+$/i.test(u) : !afHtml.value && !afText.value.trim()) {
    errors.value = { ...errors.value, autofill: kind === 'url' ? t('v5.import.urlInvalid') : t('v5.import.pasteEmpty') }
    return
  }
  afBusy.value = true
  const { autofill: _drop, ...rest } = errors.value
  errors.value = rest
  try {
    const d = await (kind === 'url'
      ? apiV5.products.importFetch(u)
      : apiV5.products.importParse({ ...(afHtml.value ? { html: afHtml.value } : { text: afText.value }), ...(/^https?:\/\/\S+$/i.test(u) ? { base_url: u } : {}) }))
    const chosen = isNew.value ? d.images.filter((i) => safeUrl(i.url)).slice(0, MAX_AUTOFILL_IMAGES) : []
    const { map, failed } = await copyImages(chosen, apiV5.content.fetchImage, safeUrl)
    if (failed) ElMessage.warning({ message: t('v5.autofill.imagesFailed', { n: failed }), grouping: true })
    const out = draftToFields(d, map, chosen)
    form.title_en = out.title.slice(0, 120)
    form.desc_en = out.description_md.slice(0, 5000)
    // prices only on a new product: never silently overwrite a deliberately set price / floor
    const sell = d.suggested_price_minor ?? d.price_minor
    if (isNew.value && sell != null) form.price = minorToYuan(sell)
    if (isNew.value && d.price_minor != null) {
      cost.value = minorToYuan(d.price_minor)
      form.floor_price = minorToYuan(d.price_minor)
    }
    if (isNew.value) form.images = out.images
    sourceUrl.value = (d.source_url ?? (kind === 'url' ? u : '')) || sourceUrl.value
    toastOk(t('v5.autofill.filled'))
  } catch (e) {
    toastError(e)
  } finally {
    afBusy.value = false
  }
}

const props = defineProps<{ modelValue: boolean; product: AdminProduct | null; categories: Category[]; prefill?: ProductPrefill | null; defaultEtaHours?: number }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; saved: [p: AdminProduct] }>()
const { t } = useI18n()

const tab = ref<'en' | 'zh-CN'>('en')
const usageTab = ref<'en' | 'zh-CN'>('en')
const preset = ref<FormatPresetKey>('card')
const labelsText = ref('')
const sample = ref('')
const asUnit = ref<AfterSalesUnit>('default')
const asValue = ref(1)
const sub = reactive({ on: false, policy: 'calendar_month' as 'calendar_month' | 'fixed_days', days: 30, grace: 0, email: true, telegram: true })
const saving = ref(false)
const uploading = ref(false)
const errors = ref<Record<string, string>>({})
const current = ref<AdminProduct | null>(null)
const form = reactive({
  title_en: '',
  title_zh: '',
  desc_en: '',
  desc_zh: '',
  price: '',
  floor_price: '',
  currency: 'CNY',
  delivery_mode: 'auto_card' as DeliveryMode,
  category: '',
  status: 'draft' as ProductStatus,
  images: [] as string[],
  separator: DEFAULT_SEPARATOR as string,
  redeem_url: '',
  usage_en: '',
  usage_zh: '',
  bi: structuredClone(BUYER_INPUT_DEFAULT) as BuyerInputForm,
})
const eta = ref<number | null>(null)
const manualStock = ref<number | null>(1) // manual only; empty = unlimited
const cost = ref('')
const sourceUrl = ref('')
const biTab = ref<'en' | 'zh-CN'>('en')
const biPreviewPrompt = computed(() => promptChips(form.bi.prompt_i18n[biTab.value] || form.bi.prompt_i18n.en || ''))
const biPreviewTitle = computed(() => form.bi.title_i18n[biTab.value] || form.bi.title_i18n.en || t('products.bi.defaultTitle'))
const biPlaceholder = computed(() => form.bi.placeholder_i18n[biTab.value] || form.bi.placeholder_i18n.en || '')
const biHint = computed(() => t('products.bi.fixedHint'))

watch(
  () => [props.modelValue, props.product] as const,
  ([open, p]) => {
    if (!open) return
    current.value = p
    tab.value = 'en'
    errors.value = {}
    afOpen.value = []
    afUrl.value = ''
    afText.value = ''
    afHtml.value = ''
    cardLines.value = ''
    cardResult.value = null
    cardError.value = ''
    const d = p ? null : (props.prefill ?? null)
    form.title_en = p?.title_i18n.en ?? d?.title ?? ''
    form.title_zh = p?.title_i18n['zh-CN'] ?? ''
    form.desc_en = p?.description_i18n.en ?? d?.description_md ?? ''
    form.desc_zh = p?.description_i18n['zh-CN'] ?? ''
    form.price = p?.price ?? (d?.price_minor != null ? minorToYuan(d.price_minor) : '')
    form.floor_price = p?.floor_price ?? (d ? minorToYuan(d.cost_minor ?? d.price_minor) : '')
    form.currency = p?.currency ?? 'CNY'
    form.delivery_mode = p?.delivery_mode ?? (d ? 'manual' : 'auto_card')
    form.category = p?.category ?? props.categories[0]?.key ?? ''
    form.status = p && p.status !== 'archived' ? p.status : 'draft'
    form.images = [...(p?.images ?? d?.images ?? [])]
    form.separator = p?.delivery_separator && isSeparator(p.delivery_separator) ? p.delivery_separator : DEFAULT_SEPARATOR
    const labels = formatLabels(p?.delivery_format, form.separator)
    preset.value = p?.delivery_format ? presetFor(labels) : 'card'
    labelsText.value = (labels.length ? labels : ['Card code']).join('\n')
    sample.value = ''
    form.redeem_url = p?.redeem_url ?? ''
    form.usage_en = p?.usage_i18n?.en ?? ''
    form.usage_zh = p?.usage_i18n?.['zh-CN'] ?? ''
    usageTab.value = 'en'
    manualStock.value = p ? (p.manual_stock ?? null) : 1
    eta.value = p ? (p.delivery_eta_hours ?? null) : d ? (props.defaultEtaHours ?? 24) : null
    cost.value = p?.cost_minor != null ? minorToYuan(p.cost_minor) : d?.cost_minor != null ? minorToYuan(d.cost_minor) : ''
    sourceUrl.value = p?.source_url ?? d?.source_url ?? ''
    const asf = afterSalesToForm(p?.after_sales_hours, p?.after_sales_months)
    asUnit.value = asf.unit
    asValue.value = asf.value
    const sb = p?.subscription
    sub.on = !!sb
    sub.policy = sb?.period_policy ?? 'calendar_month'
    sub.days = sb?.period_days ?? 30
    sub.grace = sb?.grace_days ?? 0
    sub.email = sb?.email_reminders ?? true
    sub.telegram = sb?.telegram_reminders ?? true
    const b = p?.buyer_input
    form.bi = {
      ...structuredClone(BUYER_INPUT_DEFAULT),
      ...(b ?? {}),
      title_i18n: { en: b?.title_i18n?.en ?? '', 'zh-CN': b?.title_i18n?.['zh-CN'] ?? '' },
      prompt_i18n: { en: b?.prompt_i18n?.en ?? '', 'zh-CN': b?.prompt_i18n?.['zh-CN'] ?? '' },
      placeholder_i18n: { en: b?.placeholder_i18n?.en ?? '', 'zh-CN': b?.placeholder_i18n?.['zh-CN'] ?? '' },
    }
    biTab.value = 'en'
  },
  { immediate: true },
)

const URL_HINT = "https://example.com"
const isNew = computed(() => !current.value)
const margin = computed(() => marginMinor(isValidAmount(form.price) ? yuanToMinor(form.price) : null, cost.value.trim() ? yuanToMinor(cost.value) : null))
const marginNeg = computed(() => (margin.value ?? 0) < 0)
const marginText = computed(() => (margin.value === null ? '' : `¥${minorToYuan(margin.value)}${marginP.value !== null ? ` (${marginP.value}%)` : ''}`))
const marginP = computed(() => marginPct(isValidAmount(form.price) ? yuanToMinor(form.price) : null, cost.value.trim() ? yuanToMinor(cost.value) : null))

const labels = computed(() =>
  labelsText.value
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean),
)
function resetEta() {
  eta.value = null
}
function onPreset(k: FormatPresetKey) {
  const p = FORMAT_PRESETS.find((x) => x.key === k)
  if (p) labelsText.value = p.labels.join('\n')
  sample.value = ''
}
const sampleLine = computed(() => sample.value || (labels.value.length ? labels.value.map((_, i) => `value${i + 1}`).join(form.separator) : 'ABCD-1234'))
const preview = computed(() => splitCard(sampleLine.value, labels.value, form.separator, (n) => t('delivery.fieldN', { n })))
function onAfterSalesUnit(u: AfterSalesUnit) {
  asValue.value = u === 'hours' ? 24 : 1
}

function validate(): boolean {
  const e: Record<string, string> = {}
  if (!form.title_en.trim()) e.title = t('products.v.titleRequired')
  if (!isValidAmount(form.price)) e.price = t('products.v.priceInvalid')
  if (!isValidAmount(form.floor_price)) e.floor_price = t('products.v.priceInvalid')
  else if (!e.price && compareAmount(form.floor_price, form.price) === 1) e.floor_price = t('products.v.floorAbovePrice')
  if (!form.category) e.category = t('products.v.categoryRequired')
  if (!isValidRedeemUrl(form.redeem_url)) e.redeem_url = t('delivery.redeemInvalid')
  if (!afterSalesValid(asUnit.value, asValue.value)) e.after_sales = t(`delivery.invalid.${asUnit.value}`)
  if (sub.on && (!Number.isInteger(sub.grace) || sub.grace < 0 || sub.grace > 30)) e.sub = t('subscription.graceInvalid')
  if (sub.on && sub.policy === 'fixed_days' && (!Number.isInteger(sub.days) || sub.days < 1 || sub.days > 366)) e.sub = t('subscription.daysInvalid')
  if (cost.value.trim() && yuanToMinor(cost.value) === null) e.cost = t('v5.amountInvalid')
  if (sourceUrl.value.trim() && !/^https?:\/\/\S+$/i.test(sourceUrl.value.trim())) e.source = t('delivery.redeemInvalid')
  if (eta.value !== null && (!Number.isInteger(eta.value) || eta.value < 1 || eta.value > 720)) e.eta = t('v5.payment.etaInvalid')
  if (validateBuyerInput(form.bi)) e.bi = t('products.bi.invalid')
  errors.value = e
  if (e.title) tab.value = 'en'
  return Object.keys(e).length === 0
}

function payload(): AdminProductInputV3 & { manual_stock: number | null; buyer_input: BuyerInputForm; delivery_eta_hours: number | null; cost_minor: number | null; source_url: string | null } {
  const usage = { en: form.usage_en.trim(), 'zh-CN': form.usage_zh.trim() }
  const trim = (o: { en: string; 'zh-CN': string }) => ({ en: o.en.trim(), 'zh-CN': o['zh-CN'].trim() })
  return {
    buyer_input: {
      ...form.bi,
      title_i18n: trim(form.bi.title_i18n),
      prompt_i18n: trim(form.bi.prompt_i18n),
      placeholder_i18n: trim(form.bi.placeholder_i18n),
    },
    manual_stock: form.delivery_mode === 'manual' ? manualStock.value : null,
    delivery_eta_hours: form.delivery_mode === 'manual' ? eta.value : null,
    cost_minor: cost.value.trim() ? yuanToMinor(cost.value) : null,
    source_url: sourceUrl.value.trim() || null,
    delivery_format: joinFormat(labels.value, form.separator),
    delivery_separator: form.separator,
    redeem_url: form.redeem_url.trim() || null,
    usage_i18n: usage,
    ...afterSalesFromForm(asUnit.value, asValue.value),
    subscription: sub.on
      ? { period_policy: sub.policy, period_days: sub.days, grace_days: sub.grace, email_reminders: sub.email, telegram_reminders: sub.telegram }
      : null,
    title_i18n: { en: form.title_en.trim(), 'zh-CN': form.title_zh.trim() },
    description_i18n: { en: form.desc_en.trim(), 'zh-CN': form.desc_zh.trim() },
    price: normalizeAmount(form.price)!,
    floor_price: normalizeAmount(form.floor_price)!,
    currency: form.currency,
    delivery_mode: form.delivery_mode,
    category: form.category,
    status: form.status,
  }
}

function dropNewImage(url: string) {
  form.images = form.images.filter((x) => x !== url)
}

async function importInlineCards(id: string) {
  if (form.delivery_mode !== 'auto_card' || !cardCount.value) return
  const lines = cardLines.value
  cardLines.value = '' // never keep plaintext around
  try {
    cardResult.value = await apiV3.products.importCards(id, { lines })
    cardError.value = ''
  } catch (e) {
    cardError.value = `${t(errorI18nKey(e))} - ${t('v5.autofill.cardsFailed')}`
    if (!cardLines.value) cardLines.value = lines // let staff retry instead of re-pasting
  }
}

async function save() {
  if (saving.value || !validate()) return
  saving.value = true
  try {
    if (current.value) {
      const p = await apiV3.products.update(current.value.id, payload())
      current.value = p
      emit('saved', p)
      toastOk(t('products.saved'))
      emit('update:modelValue', false)
    } else {
      const p = await apiV3.products.create({ ...payload(), images: [...form.images] })
      current.value = p
      form.images = [...p.images]
      emit('saved', p)
      toastOk(t('products.created'))
      await importInlineCards(p.id)
    }
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}

function upload(opt: UploadRequestOptions) {
  return uploadFile(opt.file)
}
// Ctrl+V anywhere in the open dialog (edit mode: a new product has no id to attach images to yet)
usePasteImage(
  window,
  (files) => void uploadFile(files[0]),
  computed(() => props.modelValue && !!current.value && !uploading.value),
)
function onImgDrop(e: DragEvent) {
  const f = Array.from(e.dataTransfer?.files ?? []).find((x) => x.type.startsWith('image/'))
  if (f && current.value) {
    e.preventDefault()
    void uploadFile(f)
  }
}
async function uploadFile(file: File) {
  const p = current.value
  if (!p) return
  if (!isAcceptedImage(file)) {
    toastError(new Error('upload_rejected'))
    return
  }
  uploading.value = true
  try {
    const c = await compressImage(file)
    const r = await api.products.uploadImage(p.id, c.blob, `image.${imageExtension(c.blob.type || file.type)}`)
    if (!form.images.includes(r.url)) form.images.push(r.url)
    current.value = { ...p, images: [...form.images], cover: p.cover ?? r.url }
    emit('saved', current.value)
  } catch (e) {
    toastError(e)
  } finally {
    uploading.value = false
  }
}

async function removeImage(url: string) {
  const p = current.value
  if (!p) return
  const next = form.images.filter((x) => x !== url)
  try {
    const updated = await api.products.update(p.id, { images: next })
    form.images = [...updated.images]
    current.value = updated
    emit('saved', updated)
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="isNew ? t('products.new') : t('products.edit')"
    width="min(720px, 96vw)"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-collapse v-model="afOpen" class="af">
      <el-collapse-item :title="t('v5.autofill.title')" name="af">
        <p class="hint muted">{{ t('v5.autofill.hint') }}</p>
        <div class="af-row">
          <el-input v-model="afUrl" inputmode="url" :placeholder="URL_HINT" :aria-label="t('v5.autofill.fetch')" spellcheck="false" @keyup.enter="autofill('url')" />
          <el-button type="primary" :loading="afBusy" @click="autofill('url')">{{ t('v5.autofill.fetch') }}</el-button>
        </div>
        <el-input :model-value="afText" type="textarea" :rows="4" :placeholder="t('v5.import.pastePlaceholder')" :aria-label="t('v5.autofill.parse')" class="af-paste" @paste="onAfPaste" @update:model-value="onAfInput" />
        <el-button :loading="afBusy" @click="autofill('paste')">{{ t('v5.autofill.parse') }}</el-button>
        <el-alert v-if="errors.autofill" type="error" :closable="false" :title="errors.autofill" class="af-paste" />
      </el-collapse-item>
    </el-collapse>
    <el-alert v-if="cardResult" type="success" :closable="false" show-icon :title="cardSummary" class="af-paste" />
    <el-alert v-if="cardError" type="error" :closable="false" show-icon :title="cardError" class="af-paste" />
    <el-form label-position="top" @submit.prevent="save">
      <el-tabs v-model="tab">
        <el-tab-pane :label="t('products.form.tabEn')" name="en">
          <el-form-item :label="t('products.form.title')" :error="errors.title" required>
            <el-input v-model="form.title_en" maxlength="120" show-word-limit />
          </el-form-item>
          <el-form-item :label="t('products.form.description')">
            <MarkdownEditor v-model="form.desc_en" :rows="6" :maxlength="5000" />
          </el-form-item>
        </el-tab-pane>
        <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
          <el-form-item :label="t('products.form.title')">
            <el-input v-model="form.title_zh" maxlength="120" show-word-limit />
          </el-form-item>
          <el-form-item :label="t('products.form.description')">
            <MarkdownEditor v-model="form.desc_zh" :rows="6" :maxlength="5000" />
          </el-form-item>
        </el-tab-pane>
      </el-tabs>

      <div class="grid">
        <el-form-item :label="`${t('products.form.price')} (${form.currency})`" :error="errors.price" required>
          <el-input v-model="form.price" inputmode="decimal" placeholder="19.90" />
        </el-form-item>
        <el-form-item :label="`${t('products.form.floorPrice')} (${form.currency})`" :error="errors.floor_price" required>
          <el-input v-model="form.floor_price" inputmode="decimal" placeholder="15.00" />
          <div class="hint muted">{{ t('products.form.floorHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('products.form.deliveryMode')">
          <el-radio-group v-model="form.delivery_mode" :disabled="!isNew">
            <el-radio-button value="auto_card">{{ t('deliveryMode.auto_card') }}</el-radio-button>
            <el-radio-button value="manual">{{ t('deliveryMode.manual') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('products.form.category')" :error="errors.category" required>
          <el-select v-model="form.category" filterable>
            <el-option v-for="c in categories" :key="c.key" :label="c.name" :value="c.key" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('products.form.status')">
          <el-radio-group v-model="form.status">
            <el-radio-button value="active">{{ t('products.status.active') }}</el-radio-button>
            <el-radio-button value="draft">{{ t('products.status.draft') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>

      <template v-if="form.delivery_mode === 'manual'">
        <el-form-item :label="t('v5.product.eta')" :error="errors.eta">
          <el-input-number v-model="eta" :min="1" :max="720" :placeholder="String(defaultEtaHours ?? 24)" />
          <span class="muted sw">{{ t('site.hours') }} ({{ t('v5.product.etaDefault', { h: defaultEtaHours ?? 24 }) }})</span>
          <el-button v-if="eta !== null" link type="primary" @click="resetEta">{{ t('v5.product.etaReset') }}</el-button>
          <div class="hint muted">{{ t('v5.product.noRush') }}</div>
        </el-form-item>
        <el-form-item :label="t('v5.product.manualStock')">
          <el-input-number v-model="manualStock" :min="0" :max="1000000" :placeholder="t('v5.product.unlimited')" />
          <div class="hint muted">{{ t('v5.product.manualStockHint') }}</div>
        </el-form-item>
      </template>

      <el-form-item v-if="isNew && form.delivery_mode === 'auto_card' && !cardResult" :label="t('v5.autofill.cardsLabel')">
        <el-input v-model="cardLines" type="textarea" :rows="5" spellcheck="false" autocomplete="off" class="mono" :placeholder="t('inventory.placeholder')" />
        <div class="hint muted">{{ t('v5.autofill.cardsHint') }} ({{ cardCount }})</div>
      </el-form-item>

      <h3 class="sec">{{ t('v5.product.sourcing') }}</h3>
      <p class="hint muted">{{ t('v5.product.sourcingNote') }}</p>
      <div class="grid">
        <el-form-item :label="`${t('v5.product.cost')} (${form.currency})`" :error="errors.cost">
          <el-input v-model="cost" inputmode="decimal" placeholder="12.00" />
        </el-form-item>
        <el-form-item :label="t('v5.product.margin')">
          <span v-if="marginText" :class="marginNeg ? 'neg' : 'pos'">{{ marginText }}</span>
          <span v-else class="muted">—</span>
        </el-form-item>
      </div>
      <el-form-item :label="t('v5.product.sourceUrl')" :error="errors.source">
        <el-input v-model="sourceUrl" maxlength="500" inputmode="url" :placeholder="URL_HINT" />
      </el-form-item>

      <h3 class="sec">{{ t('delivery.title') }}</h3>
      <div class="grid">
        <el-form-item :label="t('delivery.preset')">
          <el-select v-model="preset" @change="onPreset">
            <el-option v-for="p in FORMAT_PRESETS" :key="p.key" :value="p.key" :label="t(`delivery.presets.${p.key}`)" />
            <el-option value="custom" :label="t('delivery.presets.custom')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('delivery.separator')">
          <el-select v-model="form.separator">
            <el-option v-for="s in SEPARATORS" :key="s.key" :value="s.value" :label="t(`delivery.sep.${s.key}`)" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item :label="t('delivery.labels')">
        <el-input v-model="labelsText" type="textarea" :rows="Math.max(2, labels.length + 1)" spellcheck="false" @input="preset = 'custom'" />
        <div class="hint muted">{{ t('delivery.labelsHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('delivery.sample')">
        <el-input v-model="sample" :placeholder="sampleLine" spellcheck="false" class="mono" />
        <table class="preview" :aria-label="t('delivery.preview')">
          <tbody>
            <tr v-for="(f, i) in preview" :key="i">
              <th scope="row" :class="{ extra: f.extra }">{{ f.label }}</th>
              <td class="mono">{{ f.value }}</td>
            </tr>
          </tbody>
        </table>
        <div class="hint muted">{{ t('delivery.previewHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('delivery.redeemUrl')" :error="errors.redeem_url">
        <el-input v-model="form.redeem_url" :placeholder="URL_HINT" maxlength="500" inputmode="url" />
      </el-form-item>
      <el-form-item :label="t('delivery.usage')">
        <el-tabs v-model="usageTab" class="full">
          <el-tab-pane :label="t('products.form.tabEn')" name="en">
            <el-input v-model="form.usage_en" type="textarea" :rows="5" maxlength="5000" show-word-limit />
          </el-tab-pane>
          <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
            <el-input v-model="form.usage_zh" type="textarea" :rows="5" maxlength="5000" show-word-limit />
          </el-tab-pane>
        </el-tabs>
        <div class="hint muted">{{ t('delivery.usageHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('delivery.afterSales')" :error="errors.after_sales">
        <el-select v-model="asUnit" style="width: 200px" @change="onAfterSalesUnit">
          <el-option v-for="u in AFTER_SALES_UNITS" :key="u" :value="u" :label="t(`delivery.unit.${u}`)" />
        </el-select>
        <el-input-number v-if="asUnit !== 'default'" v-model="asValue" :min="asUnit === 'hours' ? 0 : 1" :max="asUnit === 'hours' ? 8760 : asUnit === 'days' ? 365 : 12" class="hours" />
        <div v-if="asUnit === 'hours' && asValue === 0" class="hint muted">{{ t('delivery.as.none') }}</div>
      </el-form-item>

      <h3 class="sec">{{ t('subscription.section') }}</h3>
      <el-form-item :label="t('subscription.enabled')" :error="errors.sub">
        <el-switch v-model="sub.on" />
        <span class="hint muted sw">{{ t('subscription.enabledHint') }}</span>
      </el-form-item>
      <template v-if="sub.on">
        <el-form-item :label="t('services.policy')">
          <el-select v-model="sub.policy" style="width: 200px">
            <el-option value="calendar_month" :label="t('services.month')" />
            <el-option value="fixed_days" :label="t('services.days')" />
          </el-select>
          <el-input-number v-if="sub.policy === 'fixed_days'" v-model="sub.days" :min="1" :max="366" class="hours" />
        </el-form-item>
        <el-form-item :label="t('subscription.grace')"><el-input-number v-model="sub.grace" :min="0" :max="30" /></el-form-item>
        <el-form-item>
          <el-checkbox v-model="sub.email">{{ t('subscription.email') }}</el-checkbox>
          <el-checkbox v-model="sub.telegram">{{ t('subscription.telegram') }}</el-checkbox>
        </el-form-item>
      </template>

      <h3 class="sec">{{ t('products.bi.section') }}</h3>
      <el-form-item :label="t('products.bi.enabled')">
        <el-switch v-model="form.bi.enabled" />
        <span class="hint muted sw">{{ t('products.bi.enabledHint') }}</span>
      </el-form-item>
      <template v-if="form.bi.enabled">
        <div class="grid">
          <el-form-item :label="t('products.bi.required')"><el-switch v-model="form.bi.required" /></el-form-item>
          <el-form-item :label="t('products.bi.multiline')"><el-switch v-model="form.bi.multiline" /></el-form-item>
          <el-form-item :label="t('products.bi.maxLen')" :error="errors.bi">
            <el-input-number v-model="form.bi.max_len" :min="1" :max="4000" />
          </el-form-item>
        </div>
        <el-tabs v-model="biTab" class="full">
          <el-tab-pane v-for="L in ([['en', 'tabEn'], ['zh-CN', 'tabZh']] as const)" :key="L[0]" :label="t(`products.form.${L[1]}`)" :name="L[0]">
            <el-form-item :label="t('products.bi.title')">
              <el-input v-model="form.bi.title_i18n[L[0]]" maxlength="1000" :placeholder="t('products.bi.defaultTitle')" />
            </el-form-item>
            <el-form-item :label="t('products.bi.prompt')">
              <el-input v-model="form.bi.prompt_i18n[L[0]]" type="textarea" :rows="3" maxlength="1000" show-word-limit />
              <div class="hint muted">{{ t('products.bi.promptHint') }}</div>
            </el-form-item>
            <el-form-item :label="t('products.bi.placeholder')">
              <el-input v-model="form.bi.placeholder_i18n[L[0]]" maxlength="1000" />
            </el-form-item>
          </el-tab-pane>
        </el-tabs>
        <div class="bi-preview" :aria-label="t('products.bi.preview')">
          <div class="bi-cap muted">{{ t('products.bi.preview') }}</div>
          <div class="bi-title">{{ biPreviewTitle }}</div>
          <div class="bi-prompt">
            <template v-for="(c, i) in biPreviewPrompt" :key="i">
              <code v-if="c.kind === 'code'" class="chip">{{ c.value }}</code>
              <template v-else>{{ c.value }}</template>
            </template>
          </div>
          <div class="hint muted">{{ biHint }}</div>
          <el-input
            :type="form.bi.multiline ? 'textarea' : 'text'"
            :rows="3"
            :placeholder="biPlaceholder"
            :maxlength="form.bi.max_len"
            show-word-limit
            disabled
          />
        </div>
      </template>

      <el-form-item :label="t('products.form.images')">
        <div v-if="isNew" class="images">
          <div v-for="url in form.images" :key="url" class="img">
            <el-image :src="url" fit="cover" :alt="form.title_en" />
            <el-button class="rm" size="small" circle type="danger" :aria-label="t('products.form.removeImage')" @click="dropNewImage(url)">×</el-button>
          </div>
          <div class="muted">{{ t('products.form.saveFirst') }}</div>
        </div>
        <div v-else class="images" tabindex="0" :aria-label="t('v5.product.pasteHint')" @dragover.prevent @drop="onImgDrop">
          <div v-for="(url, i) in form.images" :key="url" class="img">
            <el-image :src="url" fit="cover" :preview-src-list="form.images" :initial-index="i" preview-teleported />
            <el-tag v-if="i === 0" size="small" class="cover-tag">{{ t('products.form.cover') }}</el-tag>
            <el-button class="rm" size="small" circle type="danger" :aria-label="t('products.form.removeImage')" @click="removeImage(url)">×</el-button>
          </div>
          <el-upload
            :show-file-list="false"
            :http-request="upload"
            accept="image/jpeg,image/png,image/webp"
            :disabled="uploading"
          >
            <el-button :loading="uploading">{{ t('products.form.upload') }}</el-button>
          </el-upload>
          <div class="hint muted full">{{ t('products.form.uploadHint') }} {{ t('v5.product.pasteHint') }} {{ t('paste.tip') }}</div>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.close') }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.af-row {
  display: flex;
  gap: 8px;
}
.af-paste {
  margin: 8px 0;
}
.af {
  margin-bottom: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  column-gap: 16px;
}
.hint {
  font-size: 12px;
  line-height: 1.4;
  margin-top: 4px;
}
.images {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.img {
  position: relative;
  width: 96px;
  height: 96px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--a-line);
}
.img .el-image {
  width: 100%;
  height: 100%;
}
.cover-tag {
  position: absolute;
  left: 4px;
  top: 4px;
}
.rm {
  position: absolute;
  right: 4px;
  top: 4px;
}
.full {
  width: 100%;
}
.sec {
  margin: 8px 0 12px;
  font-size: 15px;
  padding-top: 8px;
  border-top: 1px solid var(--a-line);
}
.preview {
  margin-top: 8px;
  border-collapse: collapse;
  font-size: 13px;
  width: 100%;
}
.preview th {
  text-align: left;
  width: 140px;
  padding: 4px 8px;
  color: var(--el-text-color-secondary);
  font-weight: 500;
  border-bottom: 1px solid var(--a-line);
}
.preview th.extra {
  font-style: italic;
}
.preview td {
  padding: 4px 8px;
  border-bottom: 1px solid var(--a-line);
  overflow-wrap: anywhere;
}
.hours {
  margin-left: 8px;
}
.pos {
  color: var(--el-color-success);
  font-weight: 600;
}
.neg {
  color: var(--el-color-danger);
  font-weight: 600;
}
.sw {
  margin-left: 10px;
}
.bi-preview {
  border: 1px dashed var(--a-line);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}
.bi-cap {
  font-size: 11px;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.bi-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.bi-prompt {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.chip {
  display: inline-block;
  padding: 1px 8px;
  margin: 0 2px;
  border-radius: 10px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  font-family: ui-monospace, monospace;
  font-size: 12px;
}
</style>
