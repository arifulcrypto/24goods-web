<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UploadRequestOptions } from 'element-plus'
import {
  compareAmount,
  compressImage,
  imageExtension,
  isAcceptedImage,
  isValidAmount,
  normalizeAmount,
  type AdminProduct,
  type AdminProductInput,
  type Category,
  type DeliveryMode,
  type ProductStatus,
} from '@24goods/shared'
import { api } from '../api'
import { toastError, toastOk } from '../ui'

const props = defineProps<{ modelValue: boolean; product: AdminProduct | null; categories: Category[] }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; saved: [p: AdminProduct] }>()
const { t } = useI18n()

const tab = ref<'en' | 'zh-CN'>('en')
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
})

watch(
  () => [props.modelValue, props.product] as const,
  ([open, p]) => {
    if (!open) return
    current.value = p
    tab.value = 'en'
    errors.value = {}
    form.title_en = p?.title_i18n.en ?? ''
    form.title_zh = p?.title_i18n['zh-CN'] ?? ''
    form.desc_en = p?.description_i18n.en ?? ''
    form.desc_zh = p?.description_i18n['zh-CN'] ?? ''
    form.price = p?.price ?? ''
    form.floor_price = p?.floor_price ?? ''
    form.currency = p?.currency ?? 'CNY'
    form.delivery_mode = p?.delivery_mode ?? 'auto_card'
    form.category = p?.category ?? props.categories[0]?.key ?? ''
    form.status = p?.status ?? 'draft'
    form.images = [...(p?.images ?? [])]
  },
  { immediate: true },
)

const isNew = computed(() => !current.value)

function validate(): boolean {
  const e: Record<string, string> = {}
  if (!form.title_en.trim()) e.title = t('products.v.titleRequired')
  if (!isValidAmount(form.price)) e.price = t('products.v.priceInvalid')
  if (!isValidAmount(form.floor_price)) e.floor_price = t('products.v.priceInvalid')
  else if (!e.price && compareAmount(form.floor_price, form.price) === 1) e.floor_price = t('products.v.floorAbovePrice')
  if (!form.category) e.category = t('products.v.categoryRequired')
  errors.value = e
  if (e.title) tab.value = 'en'
  return Object.keys(e).length === 0
}

function payload(): AdminProductInput {
  return {
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

async function save() {
  if (saving.value || !validate()) return
  saving.value = true
  try {
    if (current.value) {
      const p = await api.products.update(current.value.id, payload())
      current.value = p
      emit('saved', p)
      toastOk(t('products.saved'))
      emit('update:modelValue', false)
    } else {
      const p = await api.products.create({ ...payload(), images: [] })
      current.value = p
      form.images = [...p.images]
      emit('saved', p)
      toastOk(t('products.created'))
    }
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}

async function upload(opt: UploadRequestOptions) {
  const p = current.value
  if (!p) return
  const file = opt.file
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
    <el-form label-position="top" @submit.prevent="save">
      <el-tabs v-model="tab">
        <el-tab-pane :label="t('products.form.tabEn')" name="en">
          <el-form-item :label="t('products.form.title')" :error="errors.title" required>
            <el-input v-model="form.title_en" maxlength="120" show-word-limit />
          </el-form-item>
          <el-form-item :label="t('products.form.description')">
            <el-input v-model="form.desc_en" type="textarea" :rows="5" maxlength="5000" show-word-limit />
          </el-form-item>
        </el-tab-pane>
        <el-tab-pane :label="t('products.form.tabZh')" name="zh-CN">
          <el-form-item :label="t('products.form.title')">
            <el-input v-model="form.title_zh" maxlength="120" show-word-limit />
          </el-form-item>
          <el-form-item :label="t('products.form.description')">
            <el-input v-model="form.desc_zh" type="textarea" :rows="5" maxlength="5000" show-word-limit />
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

      <el-form-item :label="t('products.form.images')">
        <div v-if="isNew" class="muted">{{ t('products.form.saveFirst') }}</div>
        <div v-else class="images">
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
          <div class="hint muted full">{{ t('products.form.uploadHint') }}</div>
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
</style>
