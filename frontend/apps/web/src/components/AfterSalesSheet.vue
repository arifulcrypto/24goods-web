<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  AFTER_SALES_REASONS,
  IMAGE_MAX_BYTES,
  compressImage,
  imageExtension,
  isAcceptedImage,
  type AfterSales,
  type AfterSalesReason,
} from '@24goods/shared'
import { api } from '../api'
import { showSuccessToast, showToast, toastError } from '../ui'

const MAX_IMAGES = 4
const props = defineProps<{ show: boolean; orderId: string }>()
const emit = defineEmits<{ 'update:show': [v: boolean]; created: [t: AfterSales] }>()
const { t } = useI18n()

const reason = ref<AfterSalesReason>('invalid_code')
const description = ref('')
const images = ref<{ id: string; url: string }[]>([])
const uploading = ref(false)
const submitting = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const valid = computed(() => description.value.trim().length >= 5 && description.value.length <= 2000)

watch(
  () => props.show,
  (v) => {
    if (v) {
      reason.value = 'invalid_code'
      description.value = ''
      images.value = []
    }
  },
)

async function onFiles(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  for (const f of files) {
    if (images.value.length >= MAX_IMAGES) {
      showToast(t('afterSales.maxImages', { n: MAX_IMAGES }))
      break
    }
    if (!isAcceptedImage(f)) {
      showToast(t('chat.image.unsupported'))
      continue
    }
    uploading.value = true
    try {
      const c = await compressImage(f)
      if (c.blob.size > IMAGE_MAX_BYTES) {
        showToast(t('chat.image.tooLarge'))
        continue
      }
      const att = await api.upload(c.blob, { kind: 'image', filename: `image.${imageExtension(c.blob.type || f.type)}` })
      images.value = [...images.value, { id: att.id, url: att.url }]
    } catch (err) {
      toastError(err)
    } finally {
      uploading.value = false
    }
  }
}
function drop(id: string) {
  images.value = images.value.filter((x) => x.id !== id)
}
async function submit() {
  if (!valid.value || submitting.value) return
  submitting.value = true
  try {
    const ticket = await api.orders.afterSales(props.orderId, {
      reason: reason.value,
      description: description.value.trim(),
      attachment_ids: images.value.map((i) => i.id),
    })
    showSuccessToast(t('afterSales.submitted'))
    emit('created', ticket)
    emit('update:show', false)
  } catch (err) {
    toastError(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <van-popup :show="show" position="bottom" round closeable safe-area-inset-bottom teleport="body" class="buy-sheet" @update:show="emit('update:show', $event)">
    <form class="as" @submit.prevent="submit">
      <h2>{{ t('afterSales.title') }}</h2>
      <fieldset class="reasons">
        <legend class="sr-only">{{ t('afterSales.reason') }}</legend>
        <label v-for="r in AFTER_SALES_REASONS" :key="r" class="reason" :class="{ on: reason === r }">
          <input v-model="reason" type="radio" name="reason" :value="r" />
          <span>{{ t(`afterSales.reasons.${r}`) }}</span>
        </label>
      </fieldset>
      <label class="lbl" for="as-desc">{{ t('afterSales.description') }}</label>
      <textarea id="as-desc" v-model="description" rows="4" maxlength="2000" :placeholder="t('afterSales.descriptionHint')" />
      <div class="imgs">
        <div v-for="i in images" :key="i.id" class="img">
          <img :src="i.url" alt="" />
          <button type="button" :aria-label="t('common.delete')" @click="drop(i.id)">×</button>
        </div>
        <button v-if="images.length < MAX_IMAGES" type="button" class="add" :disabled="uploading" :aria-label="t('afterSales.addImage')" @click="fileInput?.click()">
          {{ uploading ? '…' : '+' }}
        </button>
        <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden @change="onFiles" />
      </div>
      <van-button block round type="primary" size="large" native-type="submit" :loading="submitting" :disabled="!valid || uploading">
        {{ t('common.submit') }}
      </van-button>
    </form>
  </van-popup>
</template>

<style scoped>
.as {
  padding: 20px 16px 16px;
}
h2 {
  font-size: 18px;
  margin: 0 0 10px;
}
.reasons {
  border: 0;
  padding: 0;
  margin: 0 0 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.reason {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  padding: 0 14px;
  border-radius: 999px;
  background: #f2f2f2;
  font-size: 13px;
  cursor: pointer;
}
.reason input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.reason.on {
  background: #1f1f1f;
  color: var(--c-accent);
  font-weight: 600;
}
.reason:focus-within {
  outline: 2px solid var(--c-accent-strong);
}
.lbl {
  display: block;
  font-size: 13px;
  margin: 8px 0 4px;
}
textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--c-line);
  border-radius: 10px;
  padding: 10px;
  font: inherit;
  font-size: 14px;
  resize: vertical;
}
.imgs {
  display: flex;
  gap: 8px;
  margin: 10px 0 14px;
  flex-wrap: wrap;
}
.img,
.add {
  width: 64px;
  height: 64px;
  border-radius: 10px;
  position: relative;
}
.img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 10px;
}
.img button {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 44px;
  height: 44px;
  background: none;
  border: 0;
  color: transparent;
  cursor: pointer;
}
.img button::before {
  content: '×';
  position: absolute;
  top: 8px;
  right: 8px;
  width: 22px;
  height: 22px;
  line-height: 20px;
  text-align: center;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  font-size: 16px;
}
.add {
  border: 1px dashed #bbb;
  background: none;
  font-size: 26px;
  color: var(--c-muted);
  cursor: pointer;
}
</style>
