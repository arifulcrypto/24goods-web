<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ProductThumb } from '@24goods/shared/ui'
import { moneyParts, type ProductSummary } from '@24goods/shared'

const props = defineProps<{ product: ProductSummary }>()
const { t, locale } = useI18n()
const price = computed(() => moneyParts(props.product.price, props.product.currency, locale.value))
</script>

<template>
  <RouterLink :to="`/p/${encodeURIComponent(product.id)}`" class="mini">
    <ProductThumb :src="product.cover" :title="product.title" :size="112" :radius="10" />
    <span class="mini-title">{{ product.title }}</span>
    <span class="price mini-price"><small v-if="product.price_from">{{ t('product.priceFromPre') }}</small><small>{{ price.symbol }}</small>{{ price.value }}<small v-if="product.price_from">{{ t('product.priceFromSuf') }}</small></span>
  </RouterLink>
</template>

<style scoped>
.mini {
  flex: none;
  width: 112px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-decoration: none;
  color: inherit;
}
.mini-title {
  font-size: 12px;
  line-height: 16px;
  height: 32px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.mini-price {
  font-size: 14px;
}
.mini-price small {
  font-size: 10px;
}
</style>
