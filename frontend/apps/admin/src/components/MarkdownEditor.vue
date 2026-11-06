<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { IMAGE_MAX_BYTES, compressImage, imageExtension } from '@24goods/shared'
import { SafeHtml } from '@24goods/shared/ui'
import { apiV4 } from '../api'
import { insertAt, prefixLines, renderMarkdownPreview, safeUrl, wrapSelection, type Edit } from '../lib/markdown'
import { toastError, toastOk } from '../ui'

const props = withDefaults(defineProps<{ modelValue: string; rows?: number; maxlength?: number; placeholder?: string }>(), {
  rows: 8,
  maxlength: 50000,
  placeholder: '',
})
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()
const { t } = useI18n()

const ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
const inputRef = ref<{ textarea?: HTMLTextAreaElement } | null>(null)
const fileRef = ref<HTMLInputElement | null>(null)
const preview = ref(false)
const uploading = ref(false)
const dragging = ref(false)
const html = computed(() => renderMarkdownPreview(props.modelValue))

function ta(): HTMLTextAreaElement | null {
  return inputRef.value?.textarea ?? null
}
function apply(fn: (text: string, s: number, e: number) => Edit) {
  const el = ta()
  const text = props.modelValue
  const s = el?.selectionStart ?? text.length
  const e = el?.selectionEnd ?? text.length
  const r = fn(text, s, e)
  emit('update:modelValue', r.text)
  void nextTick(() => {
    const t2 = ta()
    if (t2) {
      t2.focus()
      t2.setSelectionRange(r.selStart, r.selEnd)
    }
  })
}
const bold = () => apply((x, s, e) => wrapSelection(x, s, e, '**', '**', t('md.boldPlaceholder')))
const heading = () => apply((x, s, e) => prefixLines(x, s, e, '## '))
const list = () => apply((x, s, e) => prefixLines(x, s, e, '- '))
function link() {
  apply((x, s, e) => {
    const sel = x.slice(s, e) || t('md.linkText')
    const snippet = `[${sel}](https://)`
    const next = x.slice(0, s) + snippet + x.slice(e)
    // select the url part so it can be typed over
    const urlStart = s + sel.length + 3
    return { text: next, selStart: urlStart, selEnd: urlStart + 'https://'.length }
  })
}

let seq = 0
async function uploadFile(file: File) {
  if (!ACCEPT.includes(file.type)) {
    toastError(new Error('upload_rejected'))
    return
  }
  // Placeholder at the cursor while uploading; replaced by the final `![alt](url)` (or removed on failure).
  const ph = `![${t('v5.md.uploading')} #${++seq}]()`
  apply((x, s) => insertAt(x, s, `${ph}\n`))
  uploading.value = true
  const drop = () => emit('update:modelValue', props.modelValue.replace(`${ph}\n`, '').replace(ph, ''))
  try {
    const c = await compressImage(file)
    if (c.blob.size > IMAGE_MAX_BYTES) {
      drop()
      toastError(new Error('upload_rejected'))
      return
    }
    const r = await apiV4.content.uploadImage(c.blob, `image.${imageExtension(c.blob.type || file.type)}`)
    if (!safeUrl(r.url)) throw new Error('bad_url')
    const alt = t('v5.md.altDefault')
    const final = `![${alt}](${r.url})`
    const next = props.modelValue.replace(ph, final)
    if (next === props.modelValue) throw new Error('placeholder_lost')
    emit('update:modelValue', next)
    // select the alt text so it can be typed over ("image description")
    void nextTick(() => {
      const el = ta()
      const i = next.indexOf(final)
      if (el && i >= 0) {
        el.focus()
        el.setSelectionRange(i + 2, i + 2 + alt.length)
      }
    })
    toastOk(t('md.uploaded'))
  } catch (e) {
    drop()
    toastError(e)
  } finally {
    uploading.value = false
  }
}
function pickFile(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (f) void uploadFile(f)
}
function onPaste(e: ClipboardEvent) {
  const f = Array.from(e.clipboardData?.files ?? []).find((x) => x.type.startsWith('image/'))
  if (f) {
    e.preventDefault()
    void uploadFile(f)
  }
}
function onDrop(e: DragEvent) {
  dragging.value = false
  const f = Array.from(e.dataTransfer?.files ?? []).find((x) => x.type.startsWith('image/'))
  if (f) {
    e.preventDefault()
    void uploadFile(f)
  }
}
</script>

<template>
  <div class="md" :class="{ drag: dragging }">
    <div class="bar" role="toolbar" :aria-label="t('md.toolbar')">
      <el-button size="small" :disabled="preview" @click="bold"><b>B</b></el-button>
      <el-button size="small" :disabled="preview" @click="heading">H</el-button>
      <el-button size="small" :disabled="preview" @click="list">{{ t('md.list') }}</el-button>
      <el-button size="small" :disabled="preview" @click="link">{{ t('md.link') }}</el-button>
      <el-button size="small" :disabled="preview" :loading="uploading" @click="fileRef?.click()">{{ t('md.image') }}</el-button>
      <span class="spacer" />
      <el-switch v-model="preview" :active-text="t('md.preview')" size="small" />
      <input ref="fileRef" type="file" accept="image/jpeg,image/png,image/webp" hidden @change="pickFile" />
    </div>
    <div v-if="preview" class="pv"><SafeHtml :html="html" /><div v-if="!modelValue.trim()" class="muted">{{ t('md.empty') }}</div></div>
    <el-input
      v-else
      ref="inputRef"
      :model-value="modelValue"
      type="textarea"
      :rows="rows"
      :maxlength="maxlength"
      :placeholder="placeholder"
      class="mono"
      @update:model-value="emit('update:modelValue', $event)"
      @paste="onPaste"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop="onDrop"
    />
    <div class="muted hint">{{ t('md.hint') }}</div>
  </div>
</template>

<style scoped>
.md {
  width: 100%;
}
.md.drag :deep(textarea) {
  outline: 2px dashed var(--el-color-primary);
}
.bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.bar .el-button + .el-button {
  margin-left: 0;
}
.spacer {
  flex: 1;
}
.pv {
  min-height: 120px;
  padding: 8px 12px;
  border: 1px solid var(--a-line);
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.pv :deep(img) {
  max-width: 100%;
}
.hint {
  font-size: 12px;
  margin-top: 4px;
}
</style>
