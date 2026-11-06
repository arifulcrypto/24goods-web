<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { TurnstileWidget } from '@24goods/shared/ui'
import type { useTurnstile } from '../composables/useTurnstile'

/** Renders the Turnstile widget for a `useTurnstile()` handle, only when that action is protected. */
const props = defineProps<{ ts: ReturnType<typeof useTurnstile> }>()
const { locale } = useI18n()
function bind(el: unknown) {
  props.ts.widget.value = el as never
}
</script>

<template>
  <div v-if="ts.needed.value" class="ts-box">
    <TurnstileWidget
      :ref="bind"
      v-model:token="ts.token.value"
      :site-key="ts.siteKey.value"
      :action="ts.action"
      :locale="locale"
    />
  </div>
</template>

<style scoped>
.ts-box {
  padding: 8px 0;
}
</style>
