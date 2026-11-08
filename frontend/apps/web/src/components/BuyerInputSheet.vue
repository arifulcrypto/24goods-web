<script setup lang="ts">
/** §4 order-information popup. Opens before Buy now / Add to cart; emits the trimmed value. */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SmartText } from '@24goods/shared/chat'
import type { BuyerInputConfig } from '@24goods/shared'
import { showToast } from '../ui'

const props = defineProps<{ show: boolean; config: BuyerInputConfig | null; initial?: string | null; busy?: boolean }>()
const emit = defineEmits<{ 'update:show': [v: boolean]; confirm: [value: string] }>()
const { t } = useI18n()

const value = ref('')
const canPaste = typeof navigator !== 'undefined' && typeof navigator.clipboard?.readText === 'function'
watch(
  () => props.show,
  (v) => {
    if (v) value.value = props.initial ?? ''
  },
)

const maxLen = computed(() => Math.min(4000, Math.max(1, props.config?.max_len ?? 2000)))
const title = computed(() => props.config?.title?.trim() || t('binput.title'))
const trimmed = computed(() => value.value.trim())
const canConfirm = computed(() => !props.busy && (!props.config?.required || trimmed.value.length > 0))

async function paste() {
  try {
    const text = await navigator.clipboard.readText()
    if (text) value.value = text.slice(0, maxLen.value)
  } catch {
    showToast(t('binput.pasteFailed'))
  }
}
function confirm() {
  if (!canConfirm.value) return
  emit('confirm', trimmed.value.slice(0, maxLen.value))
}
</script>

<template>
  <van-popup
    :show="show"
    position="bottom"
    round
    closeable
    safe-area-inset-bottom
    teleport="body"
    class="buy-sheet"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="config" class="bi" role="dialog" aria-modal="true" :aria-label="title">
      <h2 class="bi-title">{{ title }}</h2>
      <p v-if="config.prompt" class="bi-prompt"><SmartText :text="config.prompt" code /></p>
      <p class="bi-hint muted">{{ t('binput.hint') }}</p>
      <div class="bi-field">
        <van-field
          v-model="value"
          :type="config.multiline ? 'textarea' : 'text'"
          :rows="config.multiline ? 5 : undefined"
          :autosize="config.multiline ? { minHeight: 110, maxHeight: 240 } : undefined"
          :maxlength="maxLen"
          :show-word-limit="true"
          :placeholder="config.placeholder || t('binput.placeholder')"
          autocomplete="off"
          autocapitalize="off"
          :spellcheck="false"
          class="bi-input"
        />
        <button v-if="canPaste" type="button" class="bi-paste" @click="paste">{{ t('binput.paste') }}</button>
      </div>
      <van-button block round type="primary" size="large" :disabled="!canConfirm" :loading="busy" @click="confirm">
        {{ t('binput.confirm') }}
      </van-button>
    </div>
    <div v-else class="bi" role="status"><p>{{t('binput.unavailable')}}</p><van-button block @click="emit('update:show',false)">{{t('common.close')}}</van-button></div>
  </van-popup>
</template>

<style scoped>
.bi {
  padding: 20px 16px 16px;
}
.bi-title {
  font-size: 17px;
  margin: 0 32px 10px 0;
}
.bi-prompt {
  margin: 0 0 6px;
  line-height: 1.8;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  font-size: 14px;
}
.bi-hint {
  margin: 0 0 10px;
  font-size: 12px;
}
.bi-field {
  position: relative;
  margin-bottom: 12px;
}
.bi-input {
  border: 1px solid var(--c-line);
  border-radius: 10px;
  padding-bottom: 36px;
}
.bi-paste {
  position: absolute;
  left: 8px;
  bottom: 4px;
  min-height: 44px;
  padding: 0 12px;
  border: 0;
  background: none;
  color: var(--c-price);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
</style>
