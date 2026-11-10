<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { QuoteCardPayload } from '../../types'
import { formatCountdown, formatMoney } from '../../format'
import { effectiveQuoteStatus } from '../merge'

const props = defineProps<{ quote: QuoteCardPayload; mode: 'buyer' | 'staff'; busy?: boolean }>()
const emit = defineEmits<{ accept: [q: QuoteCardPayload]; withdraw: [q: QuoteCardPayload]; openOrder: [id: string] }>()
const { t, locale } = useI18n()

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null
function syncTimer() {
  const live = props.quote.status === 'offered' && new Date(props.quote.expires_at).getTime() > Date.now()
  if (live && !timer) timer = setInterval(() => (now.value = Date.now()), 1000)
  if (!live && timer) {
    clearInterval(timer)
    timer = null
  }
}
watch(() => [props.quote.status, props.quote.expires_at], syncTimer, { immediate: true })
watch(now, syncTimer)
onBeforeUnmount(() => timer && clearInterval(timer))

const status = computed(() => effectiveQuoteStatus(props.quote, now.value))
const countdown = computed(() => (status.value === 'offered' ? formatCountdown(props.quote.expires_at, now.value) : null))
const amount = computed(() => formatMoney(props.quote.amount, props.quote.currency, locale.value))
</script>

<template>
  <div class="gc-card gc-quote" :class="`is-${status}`">
    <div class="gc-card-head">
      <span class="gc-card-kicker">{{ t('chat.quote.label') }}</span>
      <span class="gc-tag" :class="`tag-${status}`">{{ t(`quoteStatus.${status}`) }}</span>
    </div>
    <div class="gc-quote-title">{{ quote.title }}</div>
    <div v-if="quote.variant_name" class="gc-muted">{{ quote.variant_name }}</div>
    <div class="gc-quote-row">
      <span class="gc-price">{{ amount }}</span>
      <span class="gc-muted">{{ t('chat.quote.qty', { n: quote.qty }) }}</span>
    </div>
    <p v-if="quote.note" class="gc-quote-note">{{ quote.note }}</p>
    <div v-if="countdown" class="gc-quote-timer" role="timer">{{ t('chat.quote.expiresIn', { time: countdown }) }}</div>
    <div class="gc-card-actions">
      <button
        v-if="mode === 'buyer' && status === 'offered'"
        type="button"
        class="gc-btn gc-btn-primary"
        :disabled="busy"
        @click="emit('accept', quote)"
      >
        <span v-if="busy" class="gc-spin" aria-hidden="true" />{{ t('chat.quote.accept') }}
      </button>
      <button
        v-if="mode === 'staff' && status === 'offered'"
        type="button"
        class="gc-btn"
        :disabled="busy"
        @click="emit('withdraw', quote)"
      >
        {{ t('chat.quote.withdraw') }}
      </button>
      <button
        v-if="quote.order_id"
        type="button"
        class="gc-btn"
        @click="emit('openOrder', quote.order_id)"
      >
        {{ t('chat.quote.viewOrder') }}
      </button>
    </div>
  </div>
</template>
