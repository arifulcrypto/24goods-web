<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { loadTurnstile, type TurnstileApi } from '../turnstile'

/**
 * Explicit-render Cloudflare Turnstile (managed mode, flexible size). Mount it ONLY when
 * `turnstileNeeded(site, action)` is true (v-if in the parent) so the script is never loaded otherwise.
 * `token` is v-model-able; call `reset()` (via ref) after a failed submit: tokens are single use.
 */
const props = defineProps<{ siteKey: string; action?: string; locale?: string }>()
const token = defineModel<string>('token', { default: '' })
const emit = defineEmits<{ error: [] }>()
const host = ref<HTMLElement | null>(null)
const failed = ref(false)
let api: TurnstileApi | null = null
let widgetId: string | undefined

function render() {
  if (!api || !host.value || widgetId !== undefined) return
  widgetId = api.render(host.value, {
    sitekey: props.siteKey,
    action: props.action,
    size: 'flexible',
    appearance: 'always',
    language: props.locale === 'zh-CN' ? 'zh-cn' : 'en',
    callback: (t: string) => {
      token.value = t
      failed.value = false
    },
    'expired-callback': () => {
      token.value = ''
    },
    'error-callback': () => {
      token.value = ''
      failed.value = true
      emit('error')
    },
  })
}
function reset() {
  token.value = ''
  if (api && widgetId !== undefined) api.reset(widgetId)
}
async function init() {
  try {
    api = await loadTurnstile()
    failed.value = false
    render()
  } catch {
    failed.value = true
    emit('error')
  }
}
onMounted(() => void init())
watch(() => props.siteKey, () => {
  if (api && widgetId !== undefined) {
    api.remove(widgetId)
    widgetId = undefined
    token.value = ''
    render()
  }
})
onBeforeUnmount(() => {
  if (api && widgetId !== undefined) api.remove(widgetId)
  widgetId = undefined
})
defineExpose({ reset, token })
</script>

<template>
  <div class="ts-wrap">
    <div ref="host" class="ts-host" />
    <button v-if="failed" type="button" class="ts-retry" @click="init">{{ $t('turnstile.retry') }}</button>
  </div>
</template>

<style scoped>
.ts-wrap {
  min-height: 65px;
}
.ts-retry {
  min-height: 44px;
  border: 0;
  background: none;
  color: var(--c-price, #d4380d);
  font: inherit;
  cursor: pointer;
}
</style>
