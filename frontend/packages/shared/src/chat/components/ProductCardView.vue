<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ProductCardPayload } from '../../types'
import { formatMoney } from '../../format'

const props = defineProps<{ product: ProductCardPayload; compact?: boolean; actions?: boolean }>()
const emit = defineEmits<{ open: [id: string]; buy: [id: string] }>()
const { t, locale } = useI18n()
const price = computed(() => formatMoney(props.product.price, props.product.currency, locale.value))
</script>

<template>
  <div class="gc-card gc-product-wrap" :class="{ compact }">
    <button type="button" class="gc-product" @click="emit('open', product.product_id)">
      <span class="gc-product-cover">
        <img v-if="product.cover" :src="product.cover" alt="" loading="lazy" decoding="async" />
      </span>
      <span class="gc-product-body">
        <span class="gc-card-kicker">{{ t('chat.product.label') }}</span>
        <span class="gc-product-title">{{ product.title }}</span>
        <span class="gc-price">{{ price }}</span>
      </span>
    </button>
    <div v-if="actions" class="gc-card-actions">
      <button type="button" class="gc-btn" @click="emit('open', product.product_id)">{{ t('chat.product.view') }}</button>
      <button type="button" class="gc-btn gc-btn-primary" @click="emit('buy', product.product_id)">{{ t('chat.product.buy') }}</button>
    </div>
  </div>
</template>
