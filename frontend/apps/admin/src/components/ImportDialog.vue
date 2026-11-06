<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SafeHtml } from '@24goods/shared/ui'
import { apiV5 } from '../api'
import type { ImportDraft, ProductPrefill } from '../api-v5'
import { pickClipboard, rewriteImages } from '../lib/importDraft'
import { renderMarkdownPreview, safeUrl } from '../lib/markdown'
import { minorToYuan, suggestPrice, yuanToMinor } from '../lib/money'
import { toastError } from '../ui'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; create: [d: ProductPrefill] }>()
const { t } = useI18n()

const URL_HINT = 'https://'
const tab = ref<'url' | 'paste'>('url')
const url = ref('')
const pasteText = ref('')
const pasteHtml = ref('')
const baseUrl = ref('')
const busy = ref(false)
const creating = ref(false)
const draft = ref<ImportDraft | null>(null)
const markup = reactive({ kind: 'percent' as 'percent' | 'fixed', value: 200, round: 10 })
const f = reactive({ title: '', cost: '', price: '', desc: '' })
const priceTouched = ref(false)
const imgs = ref<{ url: string; alt: string; on: boolean }[]>([])
const err = ref('')

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    draft.value = null
    err.value = ''
    try {
      const s = await apiV5.site.get()
      markup.kind = s.import?.markup_kind ?? 'percent'
      markup.value = s.import?.markup_value ?? 200
      markup.round = s.import?.round_to_minor ?? 10
    } catch {
      /* defaults */
    }
  },
)

const costMinor = computed(() => (f.cost.trim() ? yuanToMinor(f.cost) : null))
const suggested = computed(() => (costMinor.value === null ? null : suggestPrice(costMinor.value, markup.kind, markup.value, markup.round)))
watch(suggested, (v) => {
  if (!priceTouched.value) f.price = v === null ? '' : minorToYuan(v)
})
const markupLabel = computed(() => (markup.kind === 'percent' ? `${markup.value / 10}%` : `¥${minorToYuan(markup.value)}`))
const descHtml = computed(() => renderMarkdownPreview(f.desc))

function onPriceInput() {
  priceTouched.value = true
}
function useSuggested() {
  priceTouched.value = false
  if (suggested.value !== null) f.price = minorToYuan(suggested.value)
}
function onPaste(e: ClipboardEvent) {
  const c = pickClipboard(e.clipboardData)
  if (c.html) {
    e.preventDefault()
    pasteHtml.value = c.html.slice(0, 2_000_000)
    pasteText.value = (e.clipboardData?.getData('text/plain') ?? '').slice(0, 20000)
  } else {
    pasteHtml.value = ''
  }
}
function onPasteInput(v: string) {
  pasteText.value = v
  pasteHtml.value = '' // manual edit: send what is visible
}
function show(d: ImportDraft) {
  draft.value = d
  f.title = d.title
  f.desc = d.description_md
  f.cost = d.price_minor !== null ? minorToYuan(d.price_minor) : ''
  priceTouched.value = false
  f.price = d.suggested_price_minor !== null ? minorToYuan(d.suggested_price_minor) : suggested.value !== null ? minorToYuan(suggested.value) : ''
  imgs.value = d.images.map((i) => ({ ...i, on: !!safeUrl(i.url) }))
}
async function run(fn: () => Promise<ImportDraft>) {
  busy.value = true
  err.value = ''
  try {
    show(await fn())
  } catch (e) {
    toastError(e)
  } finally {
    busy.value = false
  }
}
const fetchUrl = () => {
  if (!/^https?:\/\/\S+$/i.test(url.value.trim())) {
    err.value = t('v5.import.urlInvalid')
    return
  }
  return run(() => apiV5.products.importFetch(url.value.trim()))
}
const parsePaste = () => {
  if (!pasteHtml.value && !pasteText.value.trim()) {
    err.value = t('v5.import.pasteEmpty')
    return
  }
  const base = baseUrl.value.trim()
  return run(() =>
    apiV5.products.importParse({
      ...(pasteHtml.value ? { html: pasteHtml.value } : { text: pasteText.value }),
      ...(/^https?:\/\/\S+$/i.test(base) ? { base_url: base } : {}),
    }),
  )
}

async function create() {
  if (!draft.value || creating.value) return
  const e: string[] = []
  if (!f.title.trim()) e.push(t('products.v.titleRequired'))
  if (f.price.trim() && yuanToMinor(f.price) === null) e.push(t('v5.amountInvalid'))
  if (f.cost.trim() && costMinor.value === null) e.push(t('v5.amountInvalid'))
  err.value = e.join('; ')
  if (e.length) return
  creating.value = true
  try {
    const chosen = imgs.value.filter((i) => i.on)
    const map = new Map<string, string>()
    let failed = 0
    const queue = [...chosen]
    await Promise.all(
      Array.from({ length: Math.min(3, queue.length) }, async () => {
        for (let i = queue.shift(); i; i = queue.shift()) {
          try {
            const r = await apiV5.content.fetchImage(i.url)
            if (safeUrl(r.url)) map.set(i.url, r.url)
            else failed++
          } catch {
            failed++
          }
        }
      }),
    )
    if (failed) ElMessage.warning({ message: t('v5.import.imagesFailed', { n: failed }), grouping: true })
    emit('create', {
      title: f.title.trim(),
      description_md: rewriteImages(f.desc, map, chosen),
      price_minor: f.price.trim() ? yuanToMinor(f.price) : suggested.value,
      cost_minor: costMinor.value,
      source_url: draft.value.source_url ?? (tab.value === 'url' ? url.value.trim() : baseUrl.value.trim()) ?? null,
    })
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('v5.import.dialogTitle')" width="min(860px, 96vw)" destroy-on-close @update:model-value="emit('update:modelValue', $event)">
    <el-tabs v-model="tab">
      <el-tab-pane :label="t('v5.import.fromUrl')" name="url">
        <p class="muted small">{{ t('v5.import.urlNote') }}</p>
        <div class="row">
          <el-input v-model="url" inputmode="url" :placeholder="URL_HINT" spellcheck="false" @keyup.enter="fetchUrl" />
          <el-button type="primary" :loading="busy" @click="fetchUrl">{{ t('v5.import.fetch') }}</el-button>
        </div>
      </el-tab-pane>
      <el-tab-pane :label="t('v5.import.paste')" name="paste">
        <p class="muted small">{{ t('v5.import.pasteNote') }}</p>
        <el-input :model-value="pasteText" type="textarea" :rows="6" :placeholder="t('v5.import.pastePlaceholder')" @paste="onPaste" @update:model-value="onPasteInput" />
        <div class="muted small">{{ pasteHtml ? t('v5.import.gotHtml', { n: pasteHtml.length }) : t('v5.import.gotText') }}</div>
        <div class="row">
          <el-input v-model="baseUrl" inputmode="url" :placeholder="t('v5.import.baseUrl')" spellcheck="false" />
          <el-button type="primary" :loading="busy" @click="parsePaste">{{ t('v5.import.parse') }}</el-button>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-alert v-if="err" type="error" :closable="false" :title="err" class="gap" />

    <template v-if="draft">
      <el-divider />
      <el-alert v-for="(w, i) in draft.warnings" :key="i" type="warning" :closable="false" show-icon :title="w" class="gap" />
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="t('products.form.title')"><el-input v-model="f.title" maxlength="120" show-word-limit /></el-form-item>
        <div class="grid">
          <el-form-item :label="t('v5.import.cost')"><el-input v-model="f.cost" inputmode="decimal"><template #prefix>¥</template></el-input></el-form-item>
          <el-form-item :label="t('v5.import.suggested', { m: markupLabel })">
            <el-input v-model="f.price" inputmode="decimal" @input="onPriceInput"><template #prefix>¥</template></el-input>
            <el-button v-if="priceTouched && suggested !== null" link type="primary" @click="useSuggested">{{ t('v5.import.useSuggested', { p: minorToYuan(suggested) }) }}</el-button>
          </el-form-item>
        </div>
        <el-form-item v-if="imgs.length" :label="t('v5.import.images')">
          <div class="imgs">
            <div v-for="(im, i) in imgs" :key="im.url" class="im">
              <el-checkbox v-model="im.on" :aria-label="t('v5.import.useImage')" class="cb" />
              <img v-if="safeUrl(im.url)" :src="im.url" :alt="im.alt" referrerpolicy="no-referrer" loading="lazy" />
              <el-input v-model="im.alt" size="small" maxlength="200" :placeholder="t('v5.import.alt')" :aria-label="`${t('v5.import.alt')} ${i + 1}`" />
            </div>
          </div>
          <div class="muted small">{{ t('v5.import.imagesNote') }}</div>
        </el-form-item>
        <el-form-item :label="t('products.form.description')">
          <div class="desc">
            <el-input v-model="f.desc" type="textarea" :rows="8" maxlength="20000" class="mono" />
            <div class="pv"><SafeHtml :html="descHtml" /></div>
          </div>
        </el-form-item>
      </el-form>
    </template>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.close') }}</el-button>
      <el-button v-if="draft" type="primary" :loading="creating" @click="create">{{ t('v5.import.createDraft') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.small {
  font-size: 12px;
}
.gap {
  margin-bottom: 8px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  column-gap: 16px;
}
.imgs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
  width: 100%;
}
.im {
  position: relative;
  border: 1px solid var(--a-line);
  border-radius: 8px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.im img {
  width: 100%;
  height: 96px;
  object-fit: cover;
  border-radius: 6px;
  background: #f3f4f6;
}
.cb {
  position: absolute;
  left: 10px;
  top: 8px;
  z-index: 1;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 4px;
  padding: 0 4px;
}
.desc {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}
.pv {
  border: 1px solid var(--a-line);
  border-radius: 6px;
  padding: 8px 12px;
  max-height: 260px;
  overflow: auto;
  overflow-wrap: anywhere;
}
.pv :deep(img) {
  max-width: 100%;
}
@media (max-width: 760px) {
  .desc {
    grid-template-columns: 1fr;
  }
}
</style>
