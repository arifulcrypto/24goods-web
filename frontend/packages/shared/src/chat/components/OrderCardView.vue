<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { OrderCardPayload } from '../../types'
import { formatMoney } from '../../format'

const props = defineProps<{ order: OrderCardPayload }>()
const emit = defineEmits<{ open: [id: string] }>()
const { t, locale } = useI18n()
const amount = computed(() => formatMoney(props.order.amount, props.order.currency, locale.value))
</script>

<template>
  <div class="gc-card gc-order">
    <div class="gc-card-head">
      <span class="gc-card-kicker">{{ t('chat.order.label') }}</span>
      <span class="gc-tag" :class="`tag-${order.status}`">{{ t(`orderStatus.${order.status}`) }}</span>
    </div>
    <div class="gc-quote-title">{{ order.title }}</div>
    <div class="gc-quote-row"><span class="gc-price">{{ amount }}</span></div>
    <div class="gc-card-actions">
      <button type="button" class="gc-btn" @click="emit('open', order.order_id)">{{ t('chat.order.view') }}</button>
    </div>
  </div>
</template>
