<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ProductCardPayload } from '../../types'
import { formatMoney } from '../../format'

const props = defineProps<{ product: ProductCardPayload; compact?: boolean }>()
const emit = defineEmits<{ open: [id: string] }>()
const { t, locale } = useI18n()
const price = computed(() => formatMoney(props.product.price, props.product.currency, locale.value))
</script>

<template>
  <button type="button" class="gc-card gc-product" :class="{ compact }" @click="emit('open', product.product_id)">
    <span class="gc-product-cover">
      <img v-if="product.cover" :src="product.cover" alt="" loading="lazy" decoding="async" />
    </span>
    <span class="gc-product-body">
      <span class="gc-card-kicker">{{ t('chat.product.label') }}</span>
      <span class="gc-product-title">{{ product.title }}</span>
      <span class="gc-price">{{ price }}</span>
    </span>
  </button>
</template>
